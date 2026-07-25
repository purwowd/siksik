package com.siksik.agent.session

import android.content.Context
import com.siksik.agent.permission.GrantGateway

object AgentRuntime {
    @Volatile
    private var grantGateway: GrantGateway? = null

    @Volatile
    var activeSessionId: String? = null
        private set

    fun grants(context: Context): GrantGateway = grantGateway ?: synchronized(this) {
        grantGateway ?: GrantGateway(context.applicationContext).also { grantGateway = it }
    }

    fun activate(sessionId: String) {
        activeSessionId = sessionId
    }

    fun clear(sessionId: String) {
        if (activeSessionId == sessionId) {
            activeSessionId = null
        }
    }
}

