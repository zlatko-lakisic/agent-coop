# Viewer severity table

One table in `src/agentcoop/timeline.py` (`SEVERITY`) covers every event kind. The viewer uses these levels for card borders and rail markers. `update.installed` is **info**, never problem/red.

| Kind | Severity |
| --- | --- |
| message.question | info |
| message.answer | info |
| message.task | info |
| message.status | info |
| message.ack | info |
| message.patch | info |
| message.context_request | info |
| message.nudge | attention |
| message.ping | info |
| message.heartbeat | info |
| message.decline | attention |
| message.cancel | attention |
| message.review_request | info |
| message.review_verdict | info (attention when verdict is changes or cannot_verify) |
| message.move | info |
| message.unknown | info |
| progress.update | info |
| progress.review | info |
| progress.waiting | attention |
| progress.done | info |
| context.version | info |
| membership.join | info |
| membership.driver | info |
| membership.helper_add | info |
| membership.helper_remove | info |
| settings.watch | attention |
| settings.review | info |
| settings.notice | info |
| settings.approval | info |
| settings.override | attention |
| file.shared | info |
| file.held | attention |
| outbox.sent | info |
| outbox.rejected | attention |
| grade.late | attention |
| grade.answer | info |
| round.log | info |
| trace.line | info |
| stall.mail | attention |
| stall.progress | problem |
| update.check | info |
| update.installed | info |
| review.metric | info (attention when verdict is changes or cannot_verify) |

Unknown kinds fall back to info; kinds starting with `stall.` fall back to problem.
