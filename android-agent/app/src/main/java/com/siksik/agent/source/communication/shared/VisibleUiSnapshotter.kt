package com.siksik.agent.source.communication

import android.graphics.Rect
import android.view.accessibility.AccessibilityNodeInfo
import com.siksik.agent.BuildConfig
import java.util.ArrayDeque

object VisibleUiSnapshotter {
    fun snapshot(root: AccessibilityNodeInfo): List<VisibleNodeRecord> {
        val queue = ArrayDeque<Pair<AccessibilityNodeInfo, Int>>()
        val records = ArrayList<VisibleNodeRecord>(BuildConfig.MAX_UI_NODES)
        queue.add(root to 0)
        while (queue.isNotEmpty() && records.size < BuildConfig.MAX_UI_NODES) {
            val (node, depth) = queue.removeFirst()
            if (depth > BuildConfig.MAX_UI_DEPTH) continue
            val bounds = Rect()
            node.getBoundsInScreen(bounds)
            records.add(
                VisibleNodeRecord(
                    sequence = records.size,
                    depth = depth,
                    text = CommunicationPolicy.boundedText(
                        node.text,
                        BuildConfig.MAX_UI_TEXT_LENGTH,
                    ),
                    contentDescription = CommunicationPolicy.boundedText(
                        node.contentDescription,
                        BuildConfig.MAX_UI_TEXT_LENGTH,
                    ),
                    className = CommunicationPolicy.boundedText(node.className, 512),
                    viewId = CommunicationPolicy.boundedText(node.viewIdResourceName, 512),
                    left = bounds.left.coerceIn(MIN_BOUND, MAX_BOUND),
                    top = bounds.top.coerceIn(MIN_BOUND, MAX_BOUND),
                    right = bounds.right.coerceIn(MIN_BOUND, MAX_BOUND),
                    bottom = bounds.bottom.coerceIn(MIN_BOUND, MAX_BOUND),
                    clickable = node.isClickable,
                    scrollable = node.isScrollable,
                ),
            )
            if (depth == BuildConfig.MAX_UI_DEPTH) continue
            val childLimit = minOf(node.childCount, BuildConfig.MAX_UI_NODES - records.size)
            for (index in 0 until childLimit) {
                node.getChild(index)?.let { child -> queue.add(child to depth + 1) }
            }
        }
        return records
    }

    private const val MIN_BOUND = -100_000
    private const val MAX_BOUND = 100_000
}
