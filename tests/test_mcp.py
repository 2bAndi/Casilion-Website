"""The MCP endpoints, exercised through the real ASGI application with the SDK's own client."""
import asyncio
import json

import httpx2
from asgiref.sync import sync_to_async
from django.core.asgi import get_asgi_application
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from apps.accounts.models import User
from apps.mcp_server import tokens
from apps.mcp_server.asgi import build_application
from apps.mcp_server.models import AgentToken
from apps.support.models import Ticket

from .base import CassilionTestCase, make_customer, run_worker_once

BASE = "http://127.0.0.1:8000"


class Lifespan:
    """Runs the ASGI lifespan (startup/shutdown) around a test, as a real server would."""

    def __init__(self, app):
        self.app = app

    async def __aenter__(self):
        self.incoming, self.outgoing = asyncio.Queue(), asyncio.Queue()
        self.task = asyncio.create_task(self.app({"type": "lifespan", "asgi": {"version": "3.0"}}, self.incoming.get, self.outgoing.put))
        await self.incoming.put({"type": "lifespan.startup"})
        assert (await self.outgoing.get())["type"] == "lifespan.startup.complete"
        return self

    async def __aexit__(self, *exc):
        await self.incoming.put({"type": "lifespan.shutdown"})
        await self.outgoing.get()
        await self.task


def _payload(result) -> dict:
    """The dict a tool returned, from the structured content or its JSON text."""
    structured = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if structured:
        return structured.get("result", structured) if set(structured) == {"result"} else structured
    return json.loads(result.content[0].text)


class McpTests(CassilionTestCase):
    def setUp(self):
        self.user, self.account = make_customer()
        _record, self.customer_token = tokens.issue(self.user, kind=AgentToken.Kind.CUSTOMER, account=self.account, name="test agent")
        self.operator = User.objects.create_user("ops@cassilion.example", "a-long-test-password", is_staff=True)
        _record, self.operator_token = tokens.issue(self.operator, kind=AgentToken.Kind.OPERATOR)
        self.app = build_application(get_asgi_application())

    def _http(self, token=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=self.app), base_url=BASE, headers=headers)

    async def test_customer_agent_from_knowledge_to_ticket(self):
        async with Lifespan(self.app), self._http(self.customer_token) as http:
            async with Client(streamable_http_client(f"{BASE}/mcp", http_client=http)) as client:
                tools = {tool.name for tool in (await client.list_tools()).tools}
                self.assertLessEqual({"search_docs", "read_doc", "list_known_issues", "get_account", "get_license_file", "open_ticket", "get_ticket", "reply_ticket", "report_bug"}, tools)

                found = _payload(await client.call_tool("search_docs", {"query": "limit cache memory"}))
                hits = {hit["doc_id"]: hit for hit in found["results"]}
                self.assertIn("configuration#memory", hits)
                self.assertEqual(hits["configuration#memory"]["url"], f"{BASE}/docs/configuration/#memory")

                account = _payload(await client.call_tool("get_account", {}))
                self.assertEqual((account["account"], account["plan"]["code"]), ("Example GmbH", "professional"))

                license = _payload(await client.call_tool("get_license_file", {}))
                self.assertIn("BEGIN CASSILION LICENSE", license["content"])

                opened = _payload(await client.call_tool("open_ticket", {
                    "subject": "How do I limit the memory Cassilion uses?",
                    "description": "We run Cassilion on a small VM. How can I limit the memory it uses for caching?",
                    "product_version": "0.9.0",
                }))
                self.assertTrue(opened["ok"])
                self.assertEqual(opened["status"], "queued")

                await sync_to_async(run_worker_once)()
                state = _payload(await client.call_tool("get_ticket", {"ticket_id": opened["ticket_id"]}))
                self.assertEqual(state["status"], "in_review")  # shadow mode: nothing AI-written is out yet
                self.assertEqual(len(state["messages"]), 1)

                missing = _payload(await client.call_tool("get_ticket", {"ticket_id": "T-NOPE00"}))
                self.assertEqual(missing["error"], "not_found")

        ticket = await Ticket.objects.aget(public_id=opened["ticket_id"])
        self.assertEqual(ticket.channel, Ticket.Channel.MCP)

    async def test_plan_limits_apply_to_agents_too(self):
        user, account = await sync_to_async(make_customer)(email="free@example.com", plan="community", name="Free Person")
        _record, token = await sync_to_async(tokens.issue)(user, kind=AgentToken.Kind.CUSTOMER, account=account)
        async with Lifespan(self.app), self._http(token) as http:
            async with Client(streamable_http_client(f"{BASE}/mcp", http_client=http)) as client:
                args = {"subject": "Memory question", "description": "How can I limit the memory used for caching on a small machine?"}
                refused = _payload(await client.call_tool("open_ticket", args))
                self.assertEqual((refused["ok"], refused["error"]), (False, "not_entitled"))
                self.assertIn("/pricing/", refused["plans_url"])
                reported = _payload(await client.call_tool("report_bug", args))
                self.assertTrue(reported["ok"])

    async def test_endpoints_need_a_token_of_the_right_kind(self):
        call = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        async with Lifespan(self.app):
            async with self._http() as http:
                anonymous = await http.post("/mcp", json=call, headers=headers)
                self.assertEqual(anonymous.status_code, 401)
                self.assertIn("resource_metadata", anonymous.headers["www-authenticate"])
                metadata = await http.get("/.well-known/oauth-protected-resource/mcp")
                self.assertEqual(metadata.json()["resource"], f"{BASE}/mcp")
            async with self._http(self.customer_token) as http:
                self.assertEqual((await http.post("/ops/mcp", json=call, headers=headers)).status_code, 401)
            async with self._http("cas_agent_not-a-real-token") as http:
                self.assertEqual((await http.post("/mcp", json=call, headers=headers)).status_code, 401)

    async def test_revoked_token_stops_working(self):
        record = await AgentToken.objects.aget(kind=AgentToken.Kind.CUSTOMER)
        await sync_to_async(tokens.revoke)(record, self.user)
        async with Lifespan(self.app), self._http(self.customer_token) as http:
            response = await http.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                                       headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"})
            self.assertEqual(response.status_code, 401)

    async def test_operator_reviews_and_releases_through_mcp(self):
        from apps.support import services as support

        ticket = await sync_to_async(support.open_ticket)(
            self.account, self.user, channel="web", subject="How do I limit the memory Cassilion uses?",
            body="We run Cassilion on a small VM. How can I limit the memory it uses for caching?", product_version="0.9.0",
        )
        await sync_to_async(run_worker_once)()
        async with Lifespan(self.app), self._http(self.operator_token) as http:
            async with Client(streamable_http_client(f"{BASE}/ops/mcp", http_client=http)) as client:
                status = _payload(await client.call_tool("site_status", {}))
                self.assertEqual((status["review_queue"], status["auto_send"]), (1, False))
                queue = _payload(await client.call_tool("list_review_queue", {}))
                self.assertEqual(queue["tickets"][0]["ticket_id"], ticket.public_id)
                analysis = _payload(await client.call_tool("get_ticket_analysis", {"ticket_id": ticket.public_id}))
                self.assertEqual(analysis["finding"]["classification"], "configuration")
                self.assertIn("cache.max_memory", analysis["draft"]["text"])
                sent = _payload(await client.call_tool("approve_reply", {"ticket_id": ticket.public_id}))
                self.assertEqual(sent["status"], "answered")
        await ticket.arefresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.ANSWERED)
