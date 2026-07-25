package com.siksik.agent.selection

import com.siksik.agent.model.ApiException
import com.siksik.agent.session.SessionAuthenticator

class SelectionSet private constructor(val itemIds: List<String>) {
    companion object {
        fun validated(itemIds: List<String>, maxItems: Int): SelectionSet {
            val snapshot = itemIds.toList()
            if (
                snapshot.isEmpty() ||
                snapshot.size > maxItems ||
                snapshot.toSet().size != snapshot.size ||
                snapshot.any { !SessionAuthenticator.SAFE_ID.matches(it) }
            ) {
                throw ApiException("validation_error", "Daftar pilihan tidak valid.", 422)
            }
            return SelectionSet(snapshot)
        }
    }
}
