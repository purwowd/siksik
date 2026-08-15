package com.siksik.agent.source.communication

import android.content.ContentResolver
import android.database.Cursor
import android.os.Bundle
import android.os.CancellationSignal
import android.provider.ContactsContract
import android.provider.Telephony

interface SmsProviderGateway {
    fun query(lastId: Long?, notBeforeEpochMs: Long, limit: Int): Cursor?
}

class AndroidSmsProviderGateway(
    private val resolver: ContentResolver,
) : SmsProviderGateway {
    override fun query(lastId: Long?, notBeforeEpochMs: Long, limit: Int): Cursor? = resolver.query(
        Telephony.Sms.CONTENT_URI,
        SMS_PROJECTION,
        Bundle().apply {
            val clauses = mutableListOf(
                "(${Telephony.Sms.DATE} >= ? OR ${Telephony.Sms.DATE} IS NULL OR " +
                    "${Telephony.Sms.DATE} <= 0)",
            )
            val arguments = mutableListOf(notBeforeEpochMs.toString())
            if (lastId != null) {
                clauses.add("${Telephony.Sms._ID} < ?")
                arguments.add(lastId.toString())
            }
            putString(ContentResolver.QUERY_ARG_SQL_SELECTION, clauses.joinToString(" AND "))
            putStringArray(ContentResolver.QUERY_ARG_SQL_SELECTION_ARGS, arguments.toTypedArray())
            putString(ContentResolver.QUERY_ARG_SQL_SORT_ORDER, "${Telephony.Sms._ID} DESC")
            putInt(ContentResolver.QUERY_ARG_LIMIT, limit)
        },
        CancellationSignal(),
    )
}

interface ContactProviderGateway {
    fun queryContacts(lastId: Long?, limit: Int): Cursor?
    fun queryDetails(contactIds: List<Long>): Cursor?
}

class AndroidContactProviderGateway(
    private val resolver: ContentResolver,
) : ContactProviderGateway {
    override fun queryContacts(lastId: Long?, limit: Int): Cursor? = resolver.query(
        ContactsContract.Contacts.CONTENT_URI,
        CONTACT_PROJECTION,
        Bundle().apply {
            if (lastId != null) {
                putString(
                    ContentResolver.QUERY_ARG_SQL_SELECTION,
                    "${ContactsContract.Contacts._ID} < ?",
                )
                putStringArray(
                    ContentResolver.QUERY_ARG_SQL_SELECTION_ARGS,
                    arrayOf(lastId.toString()),
                )
            }
            putString(
                ContentResolver.QUERY_ARG_SQL_SORT_ORDER,
                "${ContactsContract.Contacts._ID} DESC",
            )
            putInt(ContentResolver.QUERY_ARG_LIMIT, limit)
        },
        CancellationSignal(),
    )

    override fun queryDetails(contactIds: List<Long>): Cursor? {
        if (contactIds.isEmpty()) return null
        val idPlaceholders = contactIds.joinToString(",") { "?" }
        val mimePlaceholders = CONTACT_DETAIL_MIMES.joinToString(",") { "?" }
        return resolver.query(
            ContactsContract.Data.CONTENT_URI,
            CONTACT_DETAIL_PROJECTION,
            "${ContactsContract.Data.CONTACT_ID} IN ($idPlaceholders) AND " +
                "${ContactsContract.Data.MIMETYPE} IN ($mimePlaceholders)",
            (contactIds.map(Long::toString) + CONTACT_DETAIL_MIMES).toTypedArray(),
            "${ContactsContract.Data.CONTACT_ID} ASC, ${ContactsContract.Data._ID} ASC",
        )
    }
}

const val SMS_SUBSCRIPTION_ID = "sub_id"
val SMS_PROJECTION = arrayOf(
    Telephony.Sms._ID,
    Telephony.Sms.THREAD_ID,
    Telephony.Sms.ADDRESS,
    Telephony.Sms.BODY,
    Telephony.Sms.DATE,
    Telephony.Sms.DATE_SENT,
    Telephony.Sms.TYPE,
    Telephony.Sms.STATUS,
    Telephony.Sms.READ,
    Telephony.Sms.SEEN,
    SMS_SUBSCRIPTION_ID,
)
val CONTACT_PROJECTION = arrayOf(
    ContactsContract.Contacts._ID,
    ContactsContract.Contacts.LOOKUP_KEY,
    ContactsContract.Contacts.DISPLAY_NAME_PRIMARY,
    ContactsContract.Contacts.CONTACT_LAST_UPDATED_TIMESTAMP,
)
val CONTACT_DETAIL_PROJECTION = arrayOf(
    ContactsContract.Data._ID,
    ContactsContract.Data.CONTACT_ID,
    ContactsContract.Data.MIMETYPE,
    ContactsContract.Data.DATA1,
    ContactsContract.Data.DATA2,
    ContactsContract.Data.DATA3,
    ContactsContract.Data.DATA4,
    ContactsContract.Data.DATA5,
)
val CONTACT_DETAIL_MIMES = listOf(
    ContactsContract.CommonDataKinds.Phone.CONTENT_ITEM_TYPE,
    ContactsContract.CommonDataKinds.Email.CONTENT_ITEM_TYPE,
    ContactsContract.CommonDataKinds.Organization.CONTENT_ITEM_TYPE,
)
