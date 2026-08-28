---
description: Canonical SIKSIK social media crawl capture contract (IG / X / FB)
alwaysApply: true
---

# Social crawl capture contract (authoritative)

Universal success > speed. Prefer finishing UiAutomator with complete account-owned data over staying under host budget. Do not treat soft `partial` as success when a required scope below is empty. Additive OEM paths only — never break a working Infinix/Samsung IG path to fix Xiaomi.

## Instagram (`com.instagram.android`) — VISUAL

1. **Posts:** Capture from the profile grid only. Do **not** open/tap individual posts. If post count on profile **> 3**, scroll until all posts are captured; scroll in **3-cell grid pages** using profile post count. If ≤3, no post scroll.
2. **Account:** posts count, followers, following, `@username`, bio, profile link(s).
3. **Comments:** scroll until **all** own comments are captured. If only ~4 visible and list ends, do not scroll further.
4. **Archive:** exactly **3** scrolls (story archive list), then stop.

Scope order stays: profile → posts → story archive → comments. No TEXT_ONLY white cover on Instagram.

## X / Twitter (`com.twitter.android`) — TEXT_ONLY

1. **Posts (tweets):** scroll until **all** own posts are captured (text rows only).
2. **Replies:** scroll until **all** own replies are captured.
3. **Account:** followers, following, bio, username/handle.

Solid **white TEXT_ONLY overlay** must stay up for the whole X target (including debug finished frame); hide only when leaving the target. No content screenshots. Never open other users’ profiles or tweet media viewers.

## Facebook (`com.facebook.katana`) — TEXT_ONLY

1. **Posts:** scroll until **all** own posts are captured (text only).
2. **Comments and likes/reactions:** capture both from the activity hub.
3. **Account:** display name/username, friends count.

Same solid white TEXT_ONLY overlay rules as X. No content screenshots.

## Implementation guardrails

- Gate each navigation step: verify UI before the next step; retry limited times; fail the scope loudly if the gate fails — do not skip silently.
- Exhaust scrolls by **end-of-list / duplicate viewport / empty chrome**, not by a tiny fixed extra-scroll count (except IG archive = 3).
- X/FB require SIKSIK accessibility (cover + reliable taps on restrictive OEMs).
- When editing social automation, re-read this rule and preserve universal behavior across OEMs.
