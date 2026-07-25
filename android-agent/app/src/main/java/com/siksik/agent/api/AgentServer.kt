package com.siksik.agent.api

import android.content.Context
import android.os.SystemClock
import android.util.Log
import com.siksik.agent.BuildConfig
import com.siksik.agent.model.ApiException
import com.siksik.agent.permission.GrantGateway
import com.siksik.agent.preprocessing.PreprocessingCoordinator
import com.siksik.agent.preprocessing.PreprocessingStore
import com.siksik.agent.selection.SelectionCoordinator
import com.siksik.agent.selection.SelectionPolicyCodec
import com.siksik.agent.selection.SelectionStore
import com.siksik.agent.session.SessionAuthenticator
import com.siksik.agent.source.media.MediaCatalog
import com.siksik.agent.source.inventory.InventoryController
import com.siksik.agent.source.communication.CommunicationCaptureStore
import com.siksik.agent.staging.CrawlTransferManager
import com.siksik.agent.staging.StagingManager
import fi.iki.elonen.NanoHTTPD
import org.json.JSONException

class AgentServer(
    context: Context,
    private val authenticator: SessionAuthenticator,
    grants: GrantGateway,
    onStopRequested: () -> Unit,
) : NanoHTTPD(HOST, BuildConfig.API_PORT), AutoCloseable {
    private val preprocessingStore = PreprocessingStore(context.applicationContext)
    private val selectionStore = SelectionStore(context.applicationContext)
    private val communicationStore = CommunicationCaptureStore(context.applicationContext)
    private val mediaCatalog = MediaCatalog(context.applicationContext, grants)
    private val staging = StagingManager(
        context.applicationContext,
        mediaCatalog,
        grants,
    )
    private val transfer = CrawlTransferManager(
        context.applicationContext,
        preprocessingStore,
        selectionStore,
        communicationStore,
    )
    private val inventory = InventoryController(
        context.applicationContext,
        grants,
        recordSink = preprocessingStore,
    )
    private val preprocessing = PreprocessingCoordinator(
        context.applicationContext,
        preprocessingStore,
    )
    private val selection = SelectionCoordinator(
        preprocessingStore,
        selectionStore,
    )
    private val routes = listOf(
        SessionRoutes(
            context.applicationContext,
            authenticator,
            preprocessing::capabilities,
            { policy, review -> selection.configure(SelectionPolicyCodec.parse(policy), review) },
        ) {
            selection.clearSession(authenticator.sessionId)
            inventory.clearSession(authenticator.sessionId)
            onStopRequested()
        },
        GrantRoutes(authenticator, grants),
        InventoryRoutes(authenticator, inventory),
        PreprocessingRoutes(authenticator, inventory, preprocessing),
        SelectionRoutes(authenticator, preprocessing, selection),
        TransferRoutes(authenticator, transfer),
        MediaRoutes(authenticator, mediaCatalog),
        StageRoutes(authenticator, staging),
    )

    init {
        setAsyncRunner(BoundedAsyncRunner())
    }

    override fun serve(session: IHTTPSession): Response {
        val started = SystemClock.elapsedRealtime()
        val requestId = ApiRequest.normalizeRequestId(session.headers["x-request-id"])
        val request = ApiRequest(session, requestId, authenticator)
        val response = try {
            routes.firstNotNullOfOrNull { it.handle(request) }
                ?: throw ApiException("not_found", "Endpoint tidak ditemukan.", 404)
        } catch (exception: ApiException) {
            ApiResponse.error(
                exception.status,
                exception.code,
                exception.message,
                exception.retryable,
                requestId,
            )
        } catch (_: JSONException) {
            ApiResponse.error(
                400,
                "validation_error",
                "JSON request tidak valid.",
                false,
                requestId,
            )
        } catch (exception: Exception) {
            Log.e(
                LOG_TAG,
                "event=agent_request_failed request_id=$requestId " +
                    "error_category=internal_error exception_type=${exception.javaClass.simpleName}",
            )
            ApiResponse.error(
                500,
                "internal_error",
                "Request tidak dapat diselesaikan.",
                false,
                requestId,
            )
        }
        response.addHeader("X-Request-ID", requestId)
        Log.i(
            LOG_TAG,
            "event=agent_request_completed request_id=$requestId method=${session.method.name} " +
                "status=${response.status.requestStatus} " +
                "duration_ms=${SystemClock.elapsedRealtime() - started}",
        )
        return response
    }

    override fun close() {
        stop()
        transfer.shutdown()
        staging.shutdown()
        selection.close()
        communicationStore.close()
        preprocessing.close()
        inventory.close()
        preprocessingStore.clearSession(authenticator.sessionId)
        preprocessingStore.close()
    }

    companion object {
        const val HOST = "127.0.0.1"
        private const val LOG_TAG = "SIKSIKAgent"
    }
}
