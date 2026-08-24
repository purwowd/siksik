package com.siksik.agent.staging

import org.json.JSONArray
import org.json.JSONObject

object CanonicalJson {
    fun encode(value: Any?): String = when (value) {
        null, JSONObject.NULL -> "null"
        is JSONObject -> value.keys().asSequence().sorted().joinToString(",", "{", "}") { key ->
            "${JSONObject.quote(key)}:${encode(value.get(key))}"
        }
        is JSONArray -> (0 until value.length()).joinToString(",", "[", "]") { index ->
            encode(value.get(index))
        }
        is String -> JSONObject.quote(value)
        is Boolean -> value.toString()
        is Number -> {
            if (value is Double && !value.isFinite()) error("non_finite_json_number")
            if (value is Float && !value.isFinite()) error("non_finite_json_number")
            JSONObject.numberToString(value)
        }
        else -> error("unsupported_canonical_json_value")
    }

    fun bytes(value: JSONObject): ByteArray = encode(value).toByteArray(Charsets.UTF_8)
}
