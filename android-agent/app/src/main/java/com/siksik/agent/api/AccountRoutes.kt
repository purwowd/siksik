package com.siksik.agent.api

import android.accounts.Account
import android.accounts.AccountManager
import android.content.Context
import android.content.Intent
import android.database.Cursor
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.ContactsContract
import com.siksik.agent.session.SessionAuthenticator
import fi.iki.elonen.NanoHTTPD
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class AccountRoutes(
    private val context: Context,
    private val authenticator: SessionAuthenticator,
) : AgentRoute {

    override fun handle(request: ApiRequest): NanoHTTPD.Response? {
        if (request.method == NanoHTTPD.Method.GET && request.uri == ACCOUNTS_PATH) {
            request.authenticate()
            val accounts = discoverGoogleAccounts()
            val array = JSONArray()
            accounts.forEach { acc ->
                array.put(
                    JSONObject()
                        .put("name", acc.name)
                        .put("type", acc.type)
                )
            }
            return ApiResponse.json(
                200,
                JSONObject()
                    .put("session_id", authenticator.sessionId)
                    .put("accounts", array)
            )
        }
        if (request.method == NanoHTTPD.Method.POST && request.uri == TOKEN_PATH) {
            request.authenticate()
            val body = request.jsonBody(
                setOf("account_name"),
                setOf("scope", "client_id")
            )
            val accountName = body.getString("account_name")
            val scope = body.optString(
                "scope",
                "oauth2:https://www.googleapis.com/auth/gmail.readonly"
            )
            val clientId = body.optString("client_id", "")
            val authOptions = googleAuthTokenOptions(clientId)

            val accountManager = AccountManager.get(context)
            val account = Account(accountName, "com.google")

            var authToken: String? = null
            var errorMsg: String? = null

            // 1. Check if token is already cached in AccountManager
            try {
                authToken = accountManager.peekAuthToken(account, scope)
            } catch (_: Exception) {}

            // 2. If not cached, request token with AccountManager
            if (authToken.isNullOrBlank()) {
                val latch = CountDownLatch(1)
                val handler = Handler(Looper.getMainLooper())
                try {
                    accountManager.getAuthToken(
                        account,
                        scope,
                        authOptions,
                        false,
                        { future ->
                            try {
                                val bundle: Bundle = future.result
                                authToken = bundle.getString(AccountManager.KEY_AUTHTOKEN)
                                if (authToken.isNullOrBlank()) {
                                    val intent = bundle.getParcelable<Intent>(AccountManager.KEY_INTENT)
                                    if (intent != null) {
                                        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                                        context.startActivity(intent)
                                        errorMsg = "consent_prompt_opened"
                                    }
                                }
                            } catch (e: Exception) {
                                errorMsg = e.message ?: e.javaClass.simpleName
                            } finally {
                                latch.countDown()
                            }
                        },
                        handler
                    )
                    latch.await(8, TimeUnit.SECONDS)
                } catch (e: Exception) {
                    errorMsg = e.message ?: e.javaClass.simpleName
                }
            }

            // 3. If consent prompt was opened, poll for up to 60 seconds for user/accessibility grant
            if (authToken.isNullOrBlank() && errorMsg == "consent_prompt_opened") {
                val pollDeadline = System.currentTimeMillis() + 60000
                while (System.currentTimeMillis() < pollDeadline && authToken.isNullOrBlank()) {
                    try {
                        Thread.sleep(1500)
                        authToken = accountManager.peekAuthToken(account, scope)
                        if (!authToken.isNullOrBlank()) break

                        val pollLatch = CountDownLatch(1)
                        accountManager.getAuthToken(
                            account,
                            scope,
                            authOptions,
                            false,
                            { future ->
                                try {
                                    val bundle = future.result
                                    authToken = bundle.getString(AccountManager.KEY_AUTHTOKEN)
                                } catch (_: Exception) {}
                                finally {
                                    pollLatch.countDown()
                                }
                            },
                            Handler(Looper.getMainLooper())
                        )
                        pollLatch.await(3, TimeUnit.SECONDS)
                    } catch (_: Exception) {}
                }
            }

            return if (!authToken.isNullOrBlank()) {
                ApiResponse.json(
                    200,
                    JSONObject()
                        .put("session_id", authenticator.sessionId)
                        .put("account_name", accountName)
                        .put("token", authToken)
                        .put("scope", scope)
                )
            } else {
                ApiResponse.json(
                    200,
                    JSONObject()
                        .put("session_id", authenticator.sessionId)
                        .put("account_name", accountName)
                        .put("token", JSONObject.NULL)
                        .put("error", errorMsg ?: "token_unavailable")
                )
            }
        }
        return null
    }

    /**
     * Discover Google accounts using AccountManager first, then fallback
     * to ContentResolver (contacts) if GET_ACCOUNTS permission is denied
     * (common on Xiaomi MIUI where pm grant fails with SecurityException).
     */
    private fun discoverGoogleAccounts(): List<Account> {
        val accountManager = AccountManager.get(context)
        try {
            val accounts = accountManager.getAccountsByType("com.google")
            if (accounts.isNotEmpty()) return accounts.toList()
        } catch (_: SecurityException) {
            // GET_ACCOUNTS not granted — fall through to contacts fallback
        }

        // Fallback: query ContactsContract RawContacts for com.google accounts
        val found = mutableSetOf<String>()
        var cursor: Cursor? = null
        try {
            cursor = context.contentResolver.query(
                ContactsContract.RawContacts.CONTENT_URI,
                arrayOf(
                    ContactsContract.RawContacts.ACCOUNT_NAME,
                    ContactsContract.RawContacts.ACCOUNT_TYPE,
                ),
                "${ContactsContract.RawContacts.ACCOUNT_TYPE} = ?",
                arrayOf("com.google"),
                null,
            )
            cursor?.let {
                val nameIdx = it.getColumnIndexOrThrow(ContactsContract.RawContacts.ACCOUNT_NAME)
                while (it.moveToNext()) {
                    val name = it.getString(nameIdx)
                    if (!name.isNullOrBlank()) {
                        found.add(name)
                    }
                }
            }
        } catch (_: Exception) {
            // ContentResolver query failed — return empty
        } finally {
            cursor?.close()
        }

        return found.map { Account(it, "com.google") }
    }

    private fun googleAuthTokenOptions(clientId: String): Bundle? {
        if (clientId.isBlank()) return null
        return Bundle().apply { putString("client_id", clientId) }
    }

    companion object {
        private const val ACCOUNTS_PATH = "/v1/accounts/google"
        private const val TOKEN_PATH = "/v1/accounts/google/token"
    }
}
