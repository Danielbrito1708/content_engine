from datetime import datetime

import httpx

from src.core import settings

_BASE = "https://api.buffer.com"


class BufferClient:
    def __init__(self):
        self._token = settings.env.buffer_access_token
        self._channel_id = settings.env.buffer_profile_id
        self._org_id: str | None = settings.env.buffer_org_id

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    async def _graphql(self, query: str, variables: dict | None = None) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                _BASE,
                headers=self._headers(),
                json={"query": query, "variables": variables or {}},
            )
            resp.raise_for_status()
        return resp.json()

    async def _get_org_id(self) -> str:
        if self._org_id:
            return self._org_id
        data = await self._graphql("query { account { organizations { id } } }")
        orgs = data["data"]["account"]["organizations"]
        self._org_id = orgs[0]["id"]
        return self._org_id

    async def get_pending_posts(self) -> list[dict]:
        org_id = await self._get_org_id()
        data = await self._graphql(
            """
            query GetScheduledPosts($orgId: OrganizationId!, $channelId: ChannelId!) {
                posts(
                    first: 100,
                    input: {
                        organizationId: $orgId,
                        filter: { status: [scheduled], channelIds: [$channelId] }
                    }
                ) {
                    edges { node { id dueAt } }
                }
            }
            """,
            {"orgId": org_id, "channelId": self._channel_id},
        )
        edges = data.get("data", {}).get("posts", {}).get("edges", [])
        posts = []
        for edge in edges:
            node = edge["node"]
            due_at = node.get("dueAt") or ""
            try:
                ts = int(datetime.fromisoformat(due_at.replace("Z", "+00:00")).timestamp())
            except (ValueError, TypeError):
                ts = 0
            posts.append({"id": node["id"], "due_at": ts})
        return posts

    async def create_post(self, video_url: str, caption: str, scheduled_at: datetime) -> dict:
        data = await self._graphql(
            """
            mutation CreatePost(
                $channelId: ChannelId!, $text: String!, $dueAt: DateTime!, $videoUrl: String!
            ) {
                createPost(input: {
                    channelId: $channelId,
                    text: $text,
                    schedulingType: automatic,
                    mode: customScheduled,
                    dueAt: $dueAt,
                    assets: { video: { url: $videoUrl } }
                }) {
                    __typename
                    ... on PostActionSuccess { post { id dueAt } }
                    ... on MutationError { message }
                }
            }
            """,
            {
                "channelId": self._channel_id,
                "text": caption,
                "dueAt": scheduled_at.isoformat(),
                "videoUrl": video_url,
            },
        )
        errors = data.get("errors")
        if errors:
            raise RuntimeError(f"Buffer createPost failed: {errors[0].get('message')}")
        result = data.get("data", {}).get("createPost") or {}
        if result.get("__typename") == "MutationError" or result.get("message"):
            raise RuntimeError(f"Buffer createPost rejected: {result.get('message')}")
        post_id = result.get("post", {}).get("id", "")
        if not post_id:
            raise RuntimeError(f"Buffer createPost returned no post id: {data}")
        return {"updates": [{"id": post_id}]}

    async def verify_connection(self) -> bool:
        try:
            data = await self._graphql(
                "query VerifyChannel($id: ChannelId!) { channel(input: { id: $id }) { id name } }",
                {"id": self._channel_id},
            )
            return bool(data.get("data", {}).get("channel"))
        except Exception:
            return False
