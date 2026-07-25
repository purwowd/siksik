package com.siksik.agent.preprocessing

import java.security.MessageDigest
import kotlin.math.sqrt

data class DuplicateSignal(
    val recordId: String,
    val exactSha256: String?,
    val perceptualHash: String?,
    val pixelCount: Long,
    val sizeBytes: Long,
)

class DuplicateClusterer(
    private val perceptualDistance: Int = 8,
    private val maxSignals: Int = 10_000,
) {
    init {
        require(perceptualDistance in 0..16)
        require(maxSignals > 0)
    }

    fun cluster(signals: List<DuplicateSignal>): List<DuplicateMembership> {
        require(signals.size <= maxSignals) { "duplicate_signal_limit_exceeded" }
        require(signals.map(DuplicateSignal::recordId).distinct().size == signals.size)
        require(signals.all { it.exactSha256 == null || SHA256.matches(it.exactSha256) })
        require(signals.all { it.perceptualHash == null || HASH64.matches(it.perceptualHash) })
        val ordered = signals.sortedBy(DuplicateSignal::recordId)
        val exactGroups = ordered
            .filter { it.exactSha256 != null }
            .groupBy { requireNotNull(it.exactSha256) }
            .filterValues { it.size > 1 }
        val exactMembership = mutableMapOf<String, Pair<String, String>>()
        exactGroups.forEach { (hash, members) ->
            val representative = representative(members).recordId
            val groupId = "exact_${hash.take(24)}"
            members.forEach { exactMembership[it.recordId] = groupId to representative }
        }

        val visual = ordered.filter { it.perceptualHash != null }
        val union = UnionFind(visual.size)
        val index = HammingBkTree()
        for (current in visual.indices) {
            val hash = requireNotNull(visual[current].perceptualHash)
            index.within(hash, perceptualDistance).forEach { matching ->
                union.join(current, matching)
            }
            index.add(hash, current)
        }
        val perceptualMembership = mutableMapOf<String, Pair<String, String>>()
        visual.indices.groupBy(union::root).values
            .map { indices -> indices.map(visual::get) }
            .filter { it.size > 1 }
            .forEach { members ->
                val representative = representative(members).recordId
                val groupId = stableGroupId("perceptual", members.map(DuplicateSignal::recordId))
                members.forEach { perceptualMembership[it.recordId] = groupId to representative }
            }

        return ordered.map { signal ->
            val exact = exactMembership[signal.recordId]
            val perceptual = perceptualMembership[signal.recordId]
            DuplicateMembership(
                signal.recordId,
                exact?.first,
                perceptual?.first,
                exact?.second ?: perceptual?.second,
            )
        }
    }

    private fun representative(signals: List<DuplicateSignal>): DuplicateSignal = signals.maxWith(
        compareBy<DuplicateSignal> { it.pixelCount }
            .thenBy { it.sizeBytes }
            .thenByDescending { it.recordId },
    )

    companion object {
        private val SHA256 = Regex("^[0-9a-f]{64}$")
        private val HASH64 = Regex("^[0-9a-f]{16}$")
    }
}

data class FaceSignal(
    val recordId: String,
    val faceIndex: Int,
    val confidence: Float,
    val area: Long,
    val vector: FloatArray,
)

class AnonymousFaceClusterer(
    private val minimumSimilarity: Float = 0.92f,
    private val maxSignals: Int = 4096,
) {
    init {
        require(minimumSimilarity in 0f..1f)
        require(maxSignals > 0)
    }

    fun cluster(signals: List<FaceSignal>): List<FaceClusterMembership> {
        require(signals.size <= maxSignals) { "face_signal_limit_exceeded" }
        val ordered = signals.sortedWith(compareBy(FaceSignal::recordId, FaceSignal::faceIndex))
        require(ordered.all { it.vector.isNotEmpty() && it.vector.all(Float::isFinite) })
        require(ordered.map { "${it.recordId}:${it.faceIndex}" }.distinct().size == ordered.size)
        val union = UnionFind(ordered.size)
        for (left in ordered.indices) {
            for (right in left + 1 until ordered.size) {
                if (cosine(ordered[left].vector, ordered[right].vector) >= minimumSimilarity) {
                    union.join(left, right)
                }
            }
        }
        val clustersByRecord = mutableMapOf<String, MutableList<String>>()
        ordered.indices.groupBy(union::root).values
            .map { indices -> indices.map(ordered::get) }
            .filter { it.size > 1 }
            .forEach { members ->
                val ids = members.map { "${it.recordId}:${it.faceIndex}" }
                val clusterId = stableGroupId("face", ids)
                members.forEach { signal ->
                    clustersByRecord.getOrPut(signal.recordId, ::mutableListOf).add(clusterId)
                }
            }
        return ordered.map(FaceSignal::recordId).distinct().map { recordId ->
            FaceClusterMembership(
                recordId,
                clustersByRecord[recordId]?.distinct()?.sorted().orEmpty(),
            )
        }
    }

    private fun cosine(left: FloatArray, right: FloatArray): Float {
        if (left.size != right.size) return -1f
        var dot = 0.0
        var leftNorm = 0.0
        var rightNorm = 0.0
        left.indices.forEach { index ->
            dot += left[index] * right[index]
            leftNorm += left[index] * left[index]
            rightNorm += right[index] * right[index]
        }
        if (leftNorm == 0.0 || rightNorm == 0.0) return -1f
        return (dot / (sqrt(leftNorm) * sqrt(rightNorm))).toFloat()
    }
}

private class HammingBkTree {
    private var root: Node? = null

    fun add(hash: String, index: Int) {
        val first = root
        if (first == null) {
            root = Node(hash, index)
            return
        }
        var current: Node = first
        while (true) {
            val distance = DifferenceHash.hammingDistance(hash, current.hash)
            val child = current.children[distance]
            if (child == null) {
                current.children[distance] = Node(hash, index)
                return
            }
            current = child
        }
    }

    fun within(hash: String, threshold: Int): List<Int> {
        val first = root ?: return emptyList()
        val matches = mutableListOf<Int>()
        val pending = ArrayDeque<Node>()
        pending.add(first)
        while (pending.isNotEmpty()) {
            val current = pending.removeLast()
            val distance = DifferenceHash.hammingDistance(hash, current.hash)
            if (distance <= threshold) matches.add(current.index)
            val minimum = (distance - threshold).coerceAtLeast(0)
            val maximum = (distance + threshold).coerceAtMost(64)
            current.children.forEach { (edge, child) ->
                if (edge in minimum..maximum) pending.add(child)
            }
        }
        return matches
    }

    private data class Node(
        val hash: String,
        val index: Int,
        val children: MutableMap<Int, Node> = mutableMapOf(),
    )
}

private class UnionFind(size: Int) {
    private val parents = IntArray(size) { it }

    fun root(value: Int): Int {
        var current = value
        while (parents[current] != current) {
            parents[current] = parents[parents[current]]
            current = parents[current]
        }
        return current
    }

    fun join(left: Int, right: Int) {
        val leftRoot = root(left)
        val rightRoot = root(right)
        if (leftRoot == rightRoot) return
        if (leftRoot < rightRoot) parents[rightRoot] = leftRoot else parents[leftRoot] = rightRoot
    }
}

private fun stableGroupId(prefix: String, memberIds: List<String>): String {
    val digest = MessageDigest.getInstance("SHA-256")
    digest.update(prefix.toByteArray(Charsets.UTF_8))
    memberIds.sorted().forEach { id ->
        val bytes = id.toByteArray(Charsets.UTF_8)
        digest.update(bytes.size.toString().toByteArray(Charsets.UTF_8))
        digest.update(0)
        digest.update(bytes)
    }
    return "${prefix}_${digest.digest().joinToString("") { "%02x".format(it) }.take(24)}"
}
