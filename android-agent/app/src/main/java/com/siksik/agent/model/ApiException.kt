package com.siksik.agent.model

class ApiException(
    val code: String,
    override val message: String,
    val status: Int,
    val retryable: Boolean = false,
) : RuntimeException(message)

