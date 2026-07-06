"""Reddit API 采集器"""

from datetime import datetime, timezone
from typing import Optional

import praw
from praw.models import Submission
from rich.console import Console

from config import REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT
from .base import BaseCollector

console = Console()


class RedditCollector(BaseCollector):
    def __init__(
        self,
        name: str,
        subreddit: str,
        category_hint: Optional[str] = None,
        sort: str = "hot",
        limit: int = 30,
    ):
        super().__init__(name)
        self.subreddit = subreddit
        self.category_hint = category_hint
        self.sort = sort
        self.limit = limit
        self._reddit = None

    def _get_reddit(self) -> praw.Reddit:
        if self._reddit is None:
            if not all([REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET]):
                raise RuntimeError("缺少 Reddit API 凭证，请检查 .env 文件")
            self._reddit = praw.Reddit(
                client_id=REDDIT_CLIENT_ID,
                client_secret=REDDIT_CLIENT_SECRET,
                user_agent=REDDIT_USER_AGENT,
            )
        return self._reddit

    def _extract_material(self, submission: Submission) -> list[str]:
        """提取帖子中的图片/视频链接作为素材"""
        materials = []

        if hasattr(submission, "preview") and submission.preview:
            images = submission.preview.get("images", [])
            for img in images:
                source = img.get("source", {})
                url = source.get("url", "")
                if url:
                    url = url.replace("&amp;", "&")
                    materials.append(url)

        if hasattr(submission, "media_metadata") and submission.media_metadata:
            for key, media in submission.media_metadata.items():
                if media.get("e") == "Image":
                    src = media.get("s", {})
                    url = src.get("u", "") or src.get("gif", "")
                    if url:
                        url = url.replace("&amp;", "&")
                        materials.append(url)

        url = submission.url or ""
        if any(url.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp")):
            materials.append(url)

        if hasattr(submission, "is_video") and submission.is_video:
            if hasattr(submission, "media") and submission.media:
                fallback = submission.media.get("reddit_video", {})
                fallback_url = fallback.get("fallback_url", "")
                if fallback_url:
                    materials.append(fallback_url)

        return materials[:5]

    def fetch(self) -> list[dict]:
        items = []
        try:
            reddit = self._get_reddit()
            sub = reddit.subreddit(self.subreddit)

            if self.sort == "hot":
                posts = sub.hot(limit=self.limit)
            elif self.sort == "new":
                posts = sub.new(limit=self.limit)
            elif self.sort == "top":
                posts = sub.top(limit=self.limit, time_filter="week")
            else:
                posts = sub.hot(limit=self.limit)
        except Exception as e:
            console.log(f"[red]Reddit 采集失败 [{self.subreddit}]: {e}[/red]")
            return items

        for post in posts:
            if post.stickied:
                continue

            published_at = datetime.fromtimestamp(post.created_utc, tz=timezone.utc)

            item = self.normalize_item(
                title=post.title,
                url=f"https://reddit.com{post.permalink}",
                source_name=f"Reddit {self.name}",
                source_type="reddit",
                published_at=published_at,
                summary=post.selftext[:500] if post.selftext else "",
                raw_data={
                    "score": post.score,
                    "num_comments": post.num_comments,
                    "author": str(post.author) if post.author else "[deleted]",
                    "subreddit": self.subreddit,
                },
                material_links=self._extract_material(post),
            )

            if self.category_hint:
                item["category"] = self.category_hint

            items.append(item)

        console.log(f"[green]Reddit [{self.subreddit}]: {len(items)} 条[/green]")
        return items


def collect_all_reddit(subreddits: list[dict]) -> list[dict]:
    """从所有 Reddit 子版采集新闻"""
    if not all([REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET]):
        console.log("[yellow]Reddit API 未配置，跳过[/yellow]")
        return []

    all_items = []
    for cfg in subreddits:
        collector = RedditCollector(
            name=cfg["name"],
            subreddit=cfg["subreddit"],
            category_hint=cfg.get("category_hint"),
            sort=cfg.get("sort", "hot"),
            limit=cfg.get("limit", 30),
        )
        all_items.extend(collector.fetch())
    return all_items
