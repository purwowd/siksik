package com.siksik.agent.permission

import android.content.Context
import com.siksik.agent.model.ApiException

class GrantGateway(context: Context) {
    private val grantStore = GrantStore(context.applicationContext)

    @Volatile
    private var coordinator: GrantCoordinator? = null

    fun attach(value: GrantCoordinator) {
        coordinator = value
    }

    fun detach(value: GrantCoordinator) {
        if (coordinator === value) {
            coordinator = null
        }
    }

    fun launch(sessionId: String, scope: String, grantId: String, maxItems: Int): GrantRecord {
        val active = coordinator ?: throw ApiException(
            "approval_ui_unavailable",
            "Buka layar persetujuan agent lalu ulangi permintaan.",
            409,
        )
        return active.launch(sessionId, scope, grantId, maxItems)
    }

    fun get(sessionId: String, grantId: String): GrantRecord {
        val record = grantStore.refreshRevocation(grantId)
        if (record.sessionId != sessionId) {
            throw ApiException("agent_session_mismatch", "Grant bukan milik sesi aktif.", 409)
        }
        return record
    }

    fun getApproved(sessionId: String, grantId: String): GrantRecord =
        grantStore.getApproved(sessionId, grantId)

    fun grantedUris(sessionId: String, grantId: String) =
        grantStore.grantedUris(sessionId, grantId)

    fun store(): GrantStore = grantStore
}
