package com.siksik.agent

import android.content.Context
import android.content.Intent
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.siksik.agent.session.BootstrapActivity
import java.net.Socket
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class AgentLoopbackTest {
    private val token = "token_abcdefghijklmnopqrstuvwxyz0123456789"
    private val sessionId = "session_fixture"

    @Test
    fun capabilitiesExposeTypedContractAndBuildMetadata() {
        startAgent()

        val response = request(
            "GET /v1/capabilities HTTP/1.1\r\n" +
                "Host: 127.0.0.1\r\n" +
                "Authorization: Bearer $token\r\n" +
                "X-Request-ID: request_fixture\r\n" +
                "Connection: close\r\n\r\n",
        )

        assertTrue(response.startsWith("HTTP/1.1 200"))
        assertTrue(response.contains("X-Request-ID: request_fixture", ignoreCase = true))
        assertTrue(response.contains("\"schema_version\":1"))
        assertTrue(response.contains("\"api_port\":38471"))
        assertTrue(response.contains("\"package_name\":\"com.siksik.agent\""))
        assertTrue(response.contains("\"source_capabilities\""))
        assertTrue(response.contains("\"preprocessing_capabilities\""))
        assertTrue(response.contains("\"agent_build_sha256\":\""))
        val capabilities = JSONObject(response.substringAfter("\r\n\r\n"))
            .getJSONObject("preprocessing_capabilities")
        listOf(
            "ocr",
            "document_text",
            "exact_hash",
            "perceptual_hash",
            "face_model",
            "object_model",
        ).forEach { name ->
            assertEquals("granted", capabilities.getJSONObject(name).getString("state"))
        }
        stopAgent()
    }

    @Test
    fun healthRequiresSessionTokenAndReturnsBuildIdentity() {
        startAgent()

        val response = request(
            "GET /v1/health HTTP/1.1\r\n" +
                "Host: 127.0.0.1\r\n" +
                "Authorization: Bearer $token\r\n" +
                "Connection: close\r\n\r\n",
        )

        assertTrue(response.startsWith("HTTP/1.1 200"))
        assertTrue(response.contains("\"state\":\"active\""))
        assertTrue(response.contains("\"agent_build_sha256\":\""))
        stopAgent()
    }

    @Test
    fun protectedRouteRejectsMissingAuthorization() {
        startAgent()

        val response = request(
            "GET /v1/capabilities HTTP/1.1\r\n" +
                "Host: 127.0.0.1\r\n" +
                "Connection: close\r\n\r\n",
        )

        assertTrue(response.startsWith("HTTP/1.1 401"))
        assertTrue(response.contains("agent_auth_missing"))
        stopAgent()
    }

    @Test
    fun crawlStartAndStatusExposeTypedMetadataOnlyContract() {
        startAgent()
        val body =
            "{\"mode\":\"quick\",\"document_grant_id\":null,\"target_packages\":[]}"
        val started = request(
            "POST /v1/sessions/$sessionId/crawl HTTP/1.1\r\n" +
                "Host: 127.0.0.1\r\n" +
                "Authorization: Bearer $token\r\n" +
                "X-Request-ID: crawl_request_fixture\r\n" +
                "Content-Type: application/json\r\n" +
                "Content-Length: ${body.toByteArray().size}\r\n" +
                "Connection: close\r\n\r\n" +
                body,
        )

        assertTrue(started.startsWith("HTTP/1.1 201"))
        assertTrue(started.contains("\"siksik_session_id\":\"$sessionId\""))
        assertTrue(started.contains("\"media_store_audio\""))
        assertTrue(started.contains("\"shared_storage_document\""))
        assertTrue(started.contains("\"document_tree\""))
        assertTrue(!started.contains("content://"))

        val status = request(
            "GET /v1/sessions/$sessionId/crawl HTTP/1.1\r\n" +
                "Host: 127.0.0.1\r\n" +
                "Authorization: Bearer $token\r\n" +
                "Connection: close\r\n\r\n",
        )
        assertTrue(status.startsWith("HTTP/1.1 200"))
        assertTrue(status.contains("\"source_progress\""))
        stopAgent()
    }

    private fun startAgent() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val intent = Intent(context, BootstrapActivity::class.java)
            .putExtra(BootstrapActivity.EXTRA_SESSION_ID, sessionId)
            .putExtra(BootstrapActivity.EXTRA_SESSION_TOKEN, token)
            .putExtra(
                BootstrapActivity.EXTRA_TOKEN_EXPIRES_AT,
                System.currentTimeMillis() + 60_000,
            )
        ActivityScenario.launch<BootstrapActivity>(intent).close()
    }

    private fun stopAgent() {
        val body = "{}"
        val response = request(
            "POST /v1/sessions/$sessionId/stop HTTP/1.1\r\n" +
                "Host: 127.0.0.1\r\n" +
                "Authorization: Bearer $token\r\n" +
                "Content-Type: application/json\r\n" +
                "Content-Length: ${body.length}\r\n" +
                "Connection: close\r\n\r\n" +
                body,
        )
        assertTrue(response.startsWith("HTTP/1.1 200"))
    }

    private fun request(payload: String): String {
        var lastError: Exception? = null
        repeat(20) {
            try {
                Socket("127.0.0.1", BuildConfig.API_PORT).use { socket ->
                    socket.soTimeout = 2_000
                    socket.getOutputStream().write(payload.toByteArray(Charsets.UTF_8))
                    socket.getOutputStream().flush()
                    return socket.getInputStream().bufferedReader().readText()
                }
            } catch (exception: Exception) {
                lastError = exception
                Thread.sleep(100)
            }
        }
        throw AssertionError("Server loopback Android agent tidak aktif.", lastError)
    }
}
