package com.siksik.agent

import com.siksik.agent.api.BoundedTaskExecutor
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BoundedTaskExecutorTest {
    @Test
    fun rejectsWorkBeyondWorkerAndQueueBounds() {
        val executor = BoundedTaskExecutor(1, 1, "bounded-test")
        val started = CountDownLatch(1)
        val release = CountDownLatch(1)
        try {
            assertTrue(
                executor.tryExecute {
                    started.countDown()
                    release.await(2, TimeUnit.SECONDS)
                },
            )
            assertTrue(started.await(1, TimeUnit.SECONDS))
            assertTrue(executor.tryExecute { release.await(2, TimeUnit.SECONDS) })
            assertFalse(executor.tryExecute {})
        } finally {
            release.countDown()
            executor.shutdownNow()
        }
    }
}
