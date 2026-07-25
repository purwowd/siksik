package com.siksik.agent.source.communication

data class SmsRecordMetadata(
    val direction: String,
    val address: String?,
    val addressIdentity: String?,
    val threadIdentity: String?,
    val messageType: Int,
    val status: Int?,
    val subscriptionId: Int?,
    val isRead: Boolean?,
    val isSeen: Boolean?,
    val sentAtEpochMs: Long?,
)

data class ContactIdentity(
    val value: String,
    val normalizedValue: String?,
    val label: String?,
)

data class ContactOrganization(
    val company: String?,
    val title: String?,
    val department: String?,
)

data class ContactRecordMetadata(
    val displayName: String?,
    val lookupIdentity: String,
    val phones: List<ContactIdentity>,
    val emails: List<ContactIdentity>,
    val organizations: List<ContactOrganization>,
    val updatedAtEpochMs: Long?,
)

data class VisibleNodeRecord(
    val sequence: Int,
    val depth: Int,
    val text: String?,
    val contentDescription: String?,
    val className: String?,
    val viewId: String?,
    val left: Int,
    val top: Int,
    val right: Int,
    val bottom: Int,
    val clickable: Boolean,
    val scrollable: Boolean,
)

data class VisibleUiRecordMetadata(
    val packageName: String,
    val socialScope: String,
    val windowId: Int,
    val activityContext: String?,
    val eventType: Int,
    val screenSequence: Long,
    val nodes: List<VisibleNodeRecord>,
    val screenshotIds: List<String>,
    val profileLinks: List<String>,
)

data class NotificationRecordMetadata(
    val packageName: String,
    val notificationIdentity: String,
    val title: String?,
    val text: String?,
    val subText: String?,
    val bigText: String?,
    val textLines: List<String>,
    val category: String?,
    val channelId: String?,
    val postTimeEpochMs: Long,
    val removedAtEpochMs: Long?,
    val updateCount: Int,
)

data class CaptureSession(
    val sessionId: String,
    val crawlId: String,
    val targetPackages: Set<String>,
    val active: Boolean,
    val accessibilityState: String,
    val accessibilityReason: String?,
    val notificationState: String,
    val notificationReason: String?,
)

data class AutomationTargetResult(
    val targetPackage: String,
    val state: String,
    val reason: String?,
    val scrollCount: Int,
    val screenshotIds: List<String>,
    val durationMs: Long,
)
