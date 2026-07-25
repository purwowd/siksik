package com.siksik.agent.api

import fi.iki.elonen.NanoHTTPD
import java.util.Collections
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ThreadFactory
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit

class BoundedTaskExecutor(
    workerCount: Int,
    queueCapacity: Int,
    threadName: String,
) {
    private val executor = ThreadPoolExecutor(
        workerCount,
        workerCount,
        30,
        TimeUnit.SECONDS,
        ArrayBlockingQueue(queueCapacity),
        ThreadFactory { runnable -> Thread(runnable, threadName).apply { isDaemon = true } },
        ThreadPoolExecutor.AbortPolicy(),
    )

    fun tryExecute(task: Runnable): Boolean = try {
        executor.execute(task)
        true
    } catch (_: RejectedExecutionException) {
        false
    }

    fun shutdownNow() {
        executor.shutdownNow()
    }
}

class BoundedAsyncRunner : NanoHTTPD.AsyncRunner {
    private val clients = Collections.synchronizedSet(mutableSetOf<NanoHTTPD.ClientHandler>())
    private val tasks = BoundedTaskExecutor(2, 8, "siksik-agent-api")

    override fun exec(code: NanoHTTPD.ClientHandler) {
        clients.add(code)
        if (!tasks.tryExecute(code)) {
            clients.remove(code)
            code.close()
        }
    }

    override fun closed(clientHandler: NanoHTTPD.ClientHandler) {
        clients.remove(clientHandler)
    }

    override fun closeAll() {
        val snapshot = synchronized(clients) { clients.toList() }
        snapshot.forEach(NanoHTTPD.ClientHandler::close)
        clients.clear()
        tasks.shutdownNow()
    }
}
