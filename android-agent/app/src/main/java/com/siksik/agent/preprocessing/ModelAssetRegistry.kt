package com.siksik.agent.preprocessing

import android.content.Context
import java.io.InputStream
import java.security.MessageDigest
import org.json.JSONObject

data class ModelDescriptor(
    val id: String,
    val name: String,
    val version: String,
    val asset: String,
    val sha256: String,
    val inputWidth: Int,
    val inputHeight: Int,
    val inputChannels: Int,
    val outputVectorSize: Int,
    val outputContract: String,
    val license: String,
    val source: String,
)

data class ModelRegistrySnapshot(
    val schemaVersion: Int,
    val runtimeName: String,
    val runtimeVersion: String,
    val models: Map<String, ModelDescriptor>,
)

fun interface ModelAssetSource {
    fun open(path: String): InputStream
}

class ModelAssetRegistry private constructor(
    private val source: ModelAssetSource,
    val snapshot: ModelRegistrySnapshot,
) {
    fun validate(id: String): EngineCapability {
        val descriptor = snapshot.models[id] ?: return EngineCapability(
            EngineAvailability.UNAVAILABLE,
            EngineIdentity("unknown_model", snapshot.runtimeVersion),
            "model_not_registered",
        )
        val identity = EngineIdentity(
            descriptor.name,
            descriptor.version,
            descriptor.asset,
            descriptor.sha256,
        )
        return try {
            val digest = source.open(descriptor.asset).use(::sha256AndValidateHeader)
            if (digest != descriptor.sha256) {
                EngineCapability(EngineAvailability.ERROR, identity, "model_hash_mismatch")
            } else {
                EngineCapability(EngineAvailability.AVAILABLE, identity)
            }
        } catch (exc: ModelAssetValidationException) {
            EngineCapability(EngineAvailability.ERROR, identity, exc.reason)
        } catch (_: java.io.IOException) {
            EngineCapability(EngineAvailability.ERROR, identity, "model_asset_unreadable")
        } catch (_: SecurityException) {
            EngineCapability(EngineAvailability.ERROR, identity, "model_asset_unreadable")
        } catch (_: RuntimeException) {
            EngineCapability(EngineAvailability.ERROR, identity, "model_validation_failed")
        }
    }

    fun requireValidated(id: String): ModelDescriptor {
        val capability = validate(id)
        if (capability.availability != EngineAvailability.AVAILABLE) {
            throw IllegalStateException(capability.reason ?: "model_unavailable")
        }
        return requireNotNull(snapshot.models[id])
    }

    private fun sha256AndValidateHeader(input: InputStream): String {
        val digest = MessageDigest.getInstance("SHA-256")
        val header = ByteArray(8)
        var headerBytes = 0
        val buffer = ByteArray(64 * 1024)
        while (true) {
            val read = input.read(buffer)
            if (read < 0) break
            if (read == 0) continue
            if (headerBytes < header.size) {
                val copy = minOf(read, header.size - headerBytes)
                buffer.copyInto(header, headerBytes, 0, copy)
                headerBytes += copy
            }
            digest.update(buffer, 0, read)
        }
        if (headerBytes != header.size || !header.copyOfRange(4, 8).contentEquals(TFLITE_MAGIC)) {
            throw ModelAssetValidationException("model_header_mismatch")
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    companion object {
        private const val REGISTRY_ASSET = "models/registry.json"
        private val TFLITE_MAGIC = byteArrayOf('T'.code.toByte(), 'F'.code.toByte(), 'L'.code.toByte(), '3'.code.toByte())
        private val SAFE_ID = Regex("^[a-z][a-z0-9_]{2,63}$")
        private val SAFE_ASSET = Regex("^[a-zA-Z0-9_./-]{1,256}$")
        private val SHA256 = Regex("^[0-9a-f]{64}$")

        fun from(context: Context): ModelAssetRegistry {
            val assets = context.applicationContext.assets
            val source = ModelAssetSource(assets::open)
            return from(source, source.open(REGISTRY_ASSET).use { it.readBytes() })
        }

        fun from(source: ModelAssetSource, registryBytes: ByteArray): ModelAssetRegistry {
            require(registryBytes.size in 2..MAX_REGISTRY_BYTES)
            val root = JSONObject(registryBytes.toString(Charsets.UTF_8))
            require(root.fieldNames() == setOf("schema_version", "runtime", "models"))
            val schemaVersion = root.getInt("schema_version")
            require(schemaVersion == 1)
            val runtime = root.getJSONObject("runtime")
            require(runtime.fieldNames() == setOf("name", "version"))
            val modelsArray = root.getJSONArray("models")
            require(modelsArray.length() in 1..16)
            val models = linkedMapOf<String, ModelDescriptor>()
            for (index in 0 until modelsArray.length()) {
                val descriptor = parseDescriptor(modelsArray.getJSONObject(index))
                require(models.put(descriptor.id, descriptor) == null)
            }
            return ModelAssetRegistry(
                source,
                ModelRegistrySnapshot(
                    schemaVersion,
                    runtime.getString("name").bounded(64),
                    runtime.getString("version").bounded(32),
                    models,
                ),
            )
        }

        private fun parseDescriptor(value: JSONObject): ModelDescriptor {
            require(value.fieldNames() == MODEL_FIELDS)
            val id = value.getString("id")
            val asset = value.getString("asset")
            val hash = value.getString("sha256")
            require(SAFE_ID.matches(id) && SAFE_ASSET.matches(asset) && SHA256.matches(hash))
            val width = value.getInt("input_width")
            val height = value.getInt("input_height")
            val channels = value.getInt("input_channels")
            val outputVectorSize = value.getInt("output_vector_size")
            require(
                width in 16..4096 &&
                    height in 16..4096 &&
                    channels in 1..4 &&
                    outputVectorSize in 0..65_536
            )
            return ModelDescriptor(
                id,
                value.getString("name").bounded(128),
                value.getString("version").bounded(64),
                asset,
                hash,
                width,
                height,
                channels,
                outputVectorSize,
                value.getString("output_contract").bounded(128),
                value.getString("license").bounded(64),
                value.getString("source").bounded(1024),
            )
        }

        private fun String.bounded(max: Int): String = also {
            require(it.isNotBlank() && it.length <= max && it.none(Char::isISOControl))
        }

        private const val MAX_REGISTRY_BYTES = 64 * 1024
        private val MODEL_FIELDS = setOf(
            "id",
            "name",
            "version",
            "asset",
            "sha256",
            "input_width",
            "input_height",
            "input_channels",
            "output_vector_size",
            "output_contract",
            "license",
            "source",
        )
    }
}

private class ModelAssetValidationException(val reason: String) : RuntimeException(reason)

private fun JSONObject.fieldNames(): Set<String> = buildSet {
    val names = keys()
    while (names.hasNext()) add(names.next())
}
