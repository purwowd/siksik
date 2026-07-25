package com.siksik.agent.preprocessing

import java.io.ByteArrayInputStream
import java.security.MessageDigest
import org.junit.Assert.assertEquals
import org.junit.Test

class ModelAssetRegistryTest {
    @Test
    fun validAssetAndRegistryAreAccepted() {
        val model = modelBytes()
        val registry = registry(model)
        val capability = registry.validate("object_detector")
        assertEquals(EngineAvailability.AVAILABLE, capability.availability)
        assertEquals(sha256(model), capability.identity.modelSha256)
    }

    @Test
    fun missingHashAndHeaderFailuresAreExplicit() {
        val model = modelBytes()
        val missing = registry(model, source = emptyMap()).validate("object_detector")
        assertEquals(EngineAvailability.ERROR, missing.availability)
        assertEquals("model_asset_unreadable", missing.reason)

        val wrongHash = registry(model, declaredHash = "0".repeat(64)).validate("object_detector")
        assertEquals("model_hash_mismatch", wrongHash.reason)

        val invalidHeader = model.copyOf().also { it[4] = 0 }
        val invalid = registry(invalidHeader).validate("object_detector")
        assertEquals("model_header_mismatch", invalid.reason)
    }

    @Test
    fun unknownModelIsUnavailable() {
        val capability = registry(modelBytes()).validate("not_registered")
        assertEquals(EngineAvailability.UNAVAILABLE, capability.availability)
        assertEquals("model_not_registered", capability.reason)
    }

    private fun registry(
        actualModel: ByteArray,
        declaredHash: String = sha256(actualModel),
        source: Map<String, ByteArray> = mapOf(MODEL_PATH to actualModel),
    ): ModelAssetRegistry {
        val json = """
            {
              "schema_version": 1,
              "runtime": {"name": "mediapipe-tasks-vision", "version": "0.10.35"},
              "models": [{
                "id": "object_detector",
                "name": "Object detector",
                "version": "1",
                "asset": "$MODEL_PATH",
                "sha256": "$declaredHash",
                "input_width": 320,
                "input_height": 320,
                "input_channels": 3,
                "output_vector_size": 0,
                "output_contract": "coco_detection_v1",
                "license": "Apache-2.0",
                "source": "https://example.invalid/model.tflite"
              }]
            }
        """.trimIndent().toByteArray()
        return ModelAssetRegistry.from(
            ModelAssetSource { path ->
                ByteArrayInputStream(source[path] ?: throw java.io.FileNotFoundException(path))
            },
            json,
        )
    }

    private fun modelBytes(): ByteArray = ByteArray(32).also { bytes ->
        bytes[4] = 'T'.code.toByte()
        bytes[5] = 'F'.code.toByte()
        bytes[6] = 'L'.code.toByte()
        bytes[7] = '3'.code.toByte()
    }

    private fun sha256(value: ByteArray): String = MessageDigest.getInstance("SHA-256")
        .digest(value)
        .joinToString("") { "%02x".format(it) }

    companion object {
        private const val MODEL_PATH = "models/object_detector.tflite"
    }
}
