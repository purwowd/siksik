package com.siksik.agent.model

import org.json.JSONObject

enum class CapabilityState(val wireName: String) {
    UNAVAILABLE("unavailable"),
    NOT_GRANTED("not_granted"),
    AWAITING_USER("awaiting_user"),
    GRANTED("granted"),
    DENIED("denied"),
    RESTRICTED("restricted"),
    ERROR("error"),
}

data class CapabilityStatus(
    val state: CapabilityState,
    val requiredForFull: Boolean,
) {
    fun toJson(): JSONObject = JSONObject()
        .put("state", state.wireName)
        .put("required_for_full", requiredForFull)
}

data class AgentCapabilitySnapshot(
    val schemaVersion: Int,
    val agentVersion: String,
    val agentBuildSha256: String,
    val apiVersion: String,
    val apiPort: Int,
    val androidApiLevel: Int,
    val packageName: String,
    val sourceCapabilities: Map<String, CapabilityStatus>,
    val preprocessingCapabilities: Map<String, CapabilityStatus>,
    val featureCapabilities: Map<String, CapabilityStatus>,
    val permissionStates: Map<String, CapabilityStatus>,
    val specialAccessStates: Map<String, CapabilityStatus>,
    val availableStorageBytes: Long,
    val activeSessionId: String,
) {
    fun toJson(): JSONObject = JSONObject()
        .put("schema_version", schemaVersion)
        .put("agent_version", agentVersion)
        .put("agent_build_sha256", agentBuildSha256)
        .put("api_version", apiVersion)
        .put("api_port", apiPort)
        .put("android_api_level", androidApiLevel)
        .put("package_name", packageName)
        .put("source_capabilities", statusMap(sourceCapabilities))
        .put("preprocessing_capabilities", statusMap(preprocessingCapabilities))
        .put("feature_capabilities", statusMap(featureCapabilities))
        .put("permission_states", statusMap(permissionStates))
        .put("special_access_states", statusMap(specialAccessStates))
        .put("available_storage_bytes", availableStorageBytes)
        .put("active_session_id", activeSessionId)

    private fun statusMap(values: Map<String, CapabilityStatus>): JSONObject = JSONObject().also {
        values.forEach { (name, status) -> it.put(name, status.toJson()) }
    }
}
