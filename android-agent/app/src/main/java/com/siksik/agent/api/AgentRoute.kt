package com.siksik.agent.api

import fi.iki.elonen.NanoHTTPD

fun interface AgentRoute {
    fun handle(request: ApiRequest): NanoHTTPD.Response?
}
