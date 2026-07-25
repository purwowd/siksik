package com.siksik.agent.api

import com.siksik.agent.model.ApiException
import com.siksik.agent.session.SessionAuthenticator
import com.siksik.agent.source.media.CatalogPage
import com.siksik.agent.source.media.MediaCatalog
import fi.iki.elonen.NanoHTTPD
import org.json.JSONArray
import org.json.JSONObject

class MediaRoutes(
    private val authenticator: SessionAuthenticator,
    private val catalog: MediaCatalog,
) : AgentRoute {
    override fun handle(request: ApiRequest): NanoHTTPD.Response? {
        val catalogMatch = CATALOG_PATH.matchEntire(request.uri)
        if (request.method == NanoHTTPD.Method.GET && catalogMatch != null) {
            request.authenticate(catalogMatch.groupValues[1])
            request.validateQuery(setOf("grant_id"), setOf("cursor", "limit", "reuse"))
            val grantId = request.query("grant_id", required = true)!!
            val limit = request.query("limit")?.toIntOrNull()
                ?: if (request.query("limit") == null) {
                    DEFAULT_PAGE_SIZE
                } else {
                    throw ApiException("validation_error", "Batas halaman katalog tidak valid.", 422)
                }
            val reuse = when (request.query("reuse")) {
                null, "false" -> false
                "true" -> true
                else -> throw ApiException("validation_error", "Nilai reuse katalog tidak valid.", 422)
            }
            val page = catalog.list(
                authenticator.sessionId,
                grantId,
                request.query("cursor"),
                limit,
                reuse,
            )
            return ApiResponse.json(200, catalogJson(page))
        }
        val thumbnailMatch = THUMBNAIL_PATH.matchEntire(request.uri)
        if (request.method == NanoHTTPD.Method.GET && thumbnailMatch != null) {
            request.authenticate(thumbnailMatch.groupValues[1])
            request.validateQuery(setOf("grant_id"), setOf("max_dimension"))
            val rawDimension = request.query("max_dimension")
            val dimension = rawDimension?.toIntOrNull()
                ?: if (rawDimension == null) {
                    DEFAULT_THUMBNAIL_DIMENSION
                } else {
                    throw ApiException("validation_error", "Dimensi thumbnail tidak valid.", 422)
                }
            return ApiResponse.bytes(
                200,
                "image/jpeg",
                catalog.thumbnail(
                    authenticator.sessionId,
                    request.query("grant_id", required = true)!!,
                    thumbnailMatch.groupValues[2],
                    dimension,
                ),
            )
        }
        return null
    }

    private fun catalogJson(page: CatalogPage): JSONObject {
        val items = JSONArray()
        page.items.forEach { item ->
            items.put(
                JSONObject()
                    .put("media_id", item.mediaId)
                    .put("display_name", item.displayName)
                    .put("mime_type", item.mimeType)
                    .put("size_bytes", item.sizeBytes ?: JSONObject.NULL)
                    .put("width", item.width ?: JSONObject.NULL)
                    .put("height", item.height ?: JSONObject.NULL)
                    .put("duration_ms", item.durationMs ?: JSONObject.NULL)
                    .put("capture_time_epoch_ms", item.captureTimeEpochMs ?: JSONObject.NULL)
                    .put("capture_time_source", item.captureTimeSource)
                    .put("date_added_epoch_ms", item.dateAddedEpochMs ?: JSONObject.NULL)
                    .put("date_modified_epoch_ms", item.dateModifiedEpochMs ?: JSONObject.NULL)
                    .put("directory_hint", item.directoryHint ?: JSONObject.NULL)
                    .put("thumbnail_available", item.thumbnailAvailable),
            )
        }
        return JSONObject()
            .put("catalog_version", page.catalogVersion)
            .put("items", items)
            .put("next_cursor", page.nextCursor ?: JSONObject.NULL)
            .put("truncated", page.truncated)
    }

    companion object {
        private const val DEFAULT_PAGE_SIZE = 50
        private const val DEFAULT_THUMBNAIL_DIMENSION = 256
        private val CATALOG_PATH = Regex("^/v1/sessions/([^/]+)/media$")
        private val THUMBNAIL_PATH = Regex("^/v1/sessions/([^/]+)/media/([^/]+)/thumbnail$")
    }
}
