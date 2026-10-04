"""Human-readable, per-language summaries for audit-log rows (#253, Part 3).

A summary is rendered on the read side from the already-resolved row context —
nothing is stored. The result is a ``{lang: sentence}`` map (e.g.
``{"en": "Asha Patil cancelled event U10 Practice on 2026-06-01 13:30 UTC."}``)
so more languages can be added later without changing the response shape.

Design:

- ``TEMPLATES`` is keyed ``language -> AuditAction -> predicate builder``. A
  builder takes a :class:`SummaryContext` and returns the *predicate* only
  (verb + object); ``_compose`` prepends the actor and appends the timestamp
  and optional IP clause uniformly.
- English (``en``) coverage is mandatory and enforced by a test
  (``set(AuditAction) == set(TEMPLATES["en"])``). A missing *non-default*
  language is skipped (its key is simply omitted) — it does not fall back to a
  generic string.
- The generic fallback (``"performed action '<action>'"``) is reserved for an
  action string that is not a known :class:`AuditAction` at all (e.g. a renamed
  legacy row). It is the last resort, never a substitute for a real template.

No actor *role* is shown (the role-at-time-of-action is not recorded); the IP
clause appears only for rows whose ``details`` carry ``ip_address`` (the auth /
admin actions).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .audit_actions import AuditAction

DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES: tuple[str, ...] = ("en",)


@dataclass(frozen=True)
class SummaryContext:
    """Resolved, render-ready facts for a single audit row."""

    actor: str
    target: str | None
    resource_label: str | None
    resource_type: str | None
    details: Mapping[str, Any]
    timestamp_ms: int

    @property
    def target_name(self) -> str:
        return self.target or "a user"

    @property
    def resource(self) -> str:
        return self.resource_label or "(unnamed)"

    @property
    def group(self) -> str:
        name = self.details.get("group_name")
        return name if isinstance(name, str) and name else "a group"

    @property
    def ip(self) -> str | None:
        value = self.details.get("ip_address")
        return value if isinstance(value, str) and value else None

    @property
    def when(self) -> str:
        return datetime.fromtimestamp(
            self.timestamp_ms / 1000, tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M UTC")

    def detail(self, key: str) -> str | None:
        value = self.details.get(key)
        return value if isinstance(value, str) and value else None


Builder = Callable[[SummaryContext], str]


def _reason(ctx: SummaryContext) -> str:
    r = ctx.detail("reason")
    return f" (reason: {r})" if r else ""


# --- predicate-builder factories --------------------------------------------


def _plain(text: str) -> Builder:
    return lambda _c: text


def _on_resource(verb: str, noun: str) -> Builder:
    return lambda c: f"{verb} {noun} {c.resource}"


def _on_target(template: str) -> Builder:
    """``template`` uses ``{t}`` for the target name."""
    return lambda c: template.format(t=c.target_name)


def _enrollment(template: str) -> Builder:
    """``template`` uses ``{t}`` (target) and ``{r}`` (event resource)."""
    return lambda c: template.format(t=c.target_name, r=c.resource)


def _enrollment_reason(template: str) -> Builder:
    return lambda c: template.format(t=c.target_name, r=c.resource) + _reason(c)


def _occurrence(template: str) -> Builder:
    """``template`` uses ``{r}`` for the event resource (occurrence rows resolve
    ``resource_label`` to the parent event's title)."""
    return lambda c: template.format(r=c.resource)


def _media_link(template: str) -> Builder:
    """``template`` uses ``{o}`` (owner type) and ``{tag}``."""

    def build(c: SummaryContext) -> str:
        owner = c.detail("ownerType") or (c.resource_type or "resource")
        tag = c.detail("tag") or ""
        return template.format(o=owner, tag=tag)

    return build


def _credit_account(template: str) -> Builder:
    """``template`` uses ``{a}`` (account code) and ``{t}`` (target name).

    The account code is the resource id, and it is what a member quotes when
    they ring up about their credit — so it belongs in the summary rather
    than being left for the reader to dig out of the details blob.
    """
    return lambda c: template.format(a=c.resource, t=c.target_name)


# --- English registry --------------------------------------------------------

_EN: dict[AuditAction, Builder] = {
    # Authentication
    AuditAction.REGISTER: _plain("registered an account"),
    AuditAction.LOGIN: _plain("logged in"),
    AuditAction.LOGOUT: _plain("logged out"),
    AuditAction.PASSWORD_CHANGED: _plain("changed their password"),
    AuditAction.PASSWORD_RESET_REQUESTED: lambda c: (
        f"requested a password reset for {c.detail('email') or 'an account'}"
    ),
    AuditAction.TOKEN_REFRESHED: _plain("refreshed their session"),
    # Admin
    AuditAction.ADMIN_PASSWORD_RESET: _on_target("reset the password for {t}"),
    # Users
    AuditAction.SUBMIT_FOR_REVIEW: _plain("submitted their profile for review"),
    AuditAction.UPDATE_USER: _on_target("updated {t}'s profile"),
    AuditAction.APPROVE_USER: _on_target("approved {t}"),
    AuditAction.BLOCK_USER: _on_target("blocked {t}"),
    AuditAction.RECONSIDER_USER: lambda c: (
        f"asked {c.target_name} to revise their submission" + _reason(c)
    ),
    AuditAction.REAPPLY: _plain("resubmitted their profile after revision"),
    AuditAction.UNBLOCK_USER: _on_target("unblocked {t}"),
    AuditAction.ASSIGN_ROLE: lambda c: (
        f"assigned the {c.detail('role') or 'a'} role to {c.target_name}"
    ),
    AuditAction.REMOVE_ROLE: lambda c: (
        f"removed the {c.detail('role') or 'a'} role from {c.target_name}"
    ),
    AuditAction.CREATE_USER: _on_target("created the account for {t}"),
    AuditAction.DELETE_USER: _on_target("deleted {t}"),
    AuditAction.HARD_DELETE_USER: _on_target("permanently deleted {t}"),
    AuditAction.RESTORE_USER: _on_target("restored {t}"),
    AuditAction.MARK_LEFT: _on_target("marked {t} as left"),
    AuditAction.REACTIVATE_USER: _on_target("reactivated {t}"),
    AuditAction.TRANSFER_SUPERADMIN: _on_target("transferred super-admin to {t}"),
    # Events
    AuditAction.CREATE_EVENT: _on_resource("created", "event"),
    AuditAction.UPDATE_EVENT: lambda c: (
        f"updated event {c.resource}"
        + (f" (changed: {', '.join(c.details)})" if c.details else "")
    ),
    AuditAction.CORRECT_EVENT: _on_resource("corrected", "event"),
    AuditAction.SPLIT_EVENT: _on_resource("split", "event"),
    AuditAction.CANCEL_EVENT: lambda c: f"cancelled event {c.resource}" + _reason(c),
    AuditAction.UNDO_CANCEL_EVENT: _on_resource("reinstated cancelled", "event"),
    AuditAction.TERMINATE_EVENT: lambda c: (
        f"terminated programme {c.resource}" + _reason(c)
    ),
    AuditAction.EXTEND_EVENT: _on_resource("moved the cutoff of", "programme"),
    AuditAction.DROP_EVENT: lambda c: f"dropped one-off {c.resource}" + _reason(c),
    AuditAction.REINSTATE_EVENT: _on_resource("reinstated dropped", "one-off"),
    AuditAction.RESCHEDULE_EVENT: _on_resource("rescheduled", "event"),
    AuditAction.SOFT_DELETE_EVENT: _on_resource("deleted", "event"),
    AuditAction.RESTORE_EVENT: _on_resource("restored", "event"),
    AuditAction.WIPEOUT_EVENT: _on_resource("permanently deleted", "event"),
    # Enrollment
    AuditAction.ENROLLMENT_INVITED: _enrollment("invited {t} to event {r}"),
    AuditAction.ENROLLMENT_ASSIGNED: _enrollment("assigned {t} to event {r}"),
    AuditAction.ENROLLMENT_ACCEPTED: _enrollment(
        "accepted the enrollment of {t} in event {r}"
    ),
    AuditAction.ENROLLMENT_DECLINED: _enrollment(
        "declined the enrollment of {t} in event {r}"
    ),
    AuditAction.ENROLLMENT_REQUESTED: _enrollment(
        "requested enrollment for {t} in event {r}"
    ),
    AuditAction.ENROLLMENT_REJECTED: _enrollment_reason(
        "rejected the enrollment of {t} in event {r}"
    ),
    AuditAction.ENROLLMENT_REMOVED: _enrollment_reason("removed {t} from event {r}"),
    AuditAction.WITHDRAWAL_REQUESTED: _enrollment_reason(
        "requested withdrawal of {t} from event {r}"
    ),
    AuditAction.WITHDRAWAL_CANCELLED: _enrollment(
        "cancelled the withdrawal request for {t} in event {r}"
    ),
    AuditAction.WITHDRAWAL_APPROVED: _enrollment(
        "approved the withdrawal of {t} from event {r}"
    ),
    AuditAction.WITHDRAWAL_REJECTED: _enrollment_reason(
        "rejected the withdrawal of {t} from event {r}"
    ),
    # Occurrences
    AuditAction.RESCHEDULE_OCCURRENCE: _occurrence(
        "rescheduled an occurrence of event {r}"
    ),
    AuditAction.CANCEL_OCCURRENCE: lambda c: (
        f"cancelled an occurrence of event {c.resource}" + _reason(c)
    ),
    AuditAction.UNDO_CANCEL_OCCURRENCE: _occurrence(
        "reinstated a cancelled occurrence of event {r}"
    ),
    AuditAction.ATTENDANCE_MARKED: lambda c: (
        f"marked {c.target_name} as {c.detail('status') or 'attended'} "
        f"for an occurrence of event {c.resource}"
    ),
    AuditAction.ATTENDANCE_CLEARED: lambda c: (
        f"cleared {c.target_name}'s attendance for an occurrence of event {c.resource}"
    ),
    AuditAction.LEAVE_REQUESTED: lambda c: (
        f"requested leave for {c.target_name} from an occurrence of event {c.resource}"
        + _reason(c)
    ),
    AuditAction.LEAVE_CANCELLED: lambda c: (
        f"cancelled the leave request for {c.target_name} "
        f"for an occurrence of event {c.resource}"
    ),
    AuditAction.LEAVE_APPROVED: lambda c: (
        f"approved leave for {c.target_name} for an occurrence of event {c.resource}"
    ),
    AuditAction.LEAVE_REJECTED: lambda c: (
        f"rejected leave for {c.target_name} for an occurrence of event {c.resource}"
        + _reason(c)
    ),
    # Groups
    AuditAction.CREATE_GROUP: _on_resource("created", "group"),
    AuditAction.UPDATE_GROUP: _on_resource("updated", "group"),
    AuditAction.SOFT_DELETE_GROUP: _on_resource("deleted", "group"),
    AuditAction.RESTORE_GROUP: _on_resource("restored", "group"),
    AuditAction.WIPEOUT_GROUP: _on_resource("permanently deleted", "group"),
    AuditAction.ADD_GROUP_MEMBER: lambda c: (
        f"added {c.target_name} to group {c.resource}"
    ),
    AuditAction.ADD_GROUP_MEMBERS_BULK: lambda c: (
        f"added {len(c.details.get('added') or [])} member(s) to group {c.resource}"
    ),
    AuditAction.REMOVE_GROUP_MEMBER: lambda c: (
        f"removed {c.target_name} from group {c.resource}"
    ),
    AuditAction.APPROVE_GROUP_JOIN_REQUEST: lambda c: (
        f"approved {c.target_name}'s request to join group {c.group}"
    ),
    AuditAction.REJECT_GROUP_JOIN_REQUEST: lambda c: (
        f"rejected {c.target_name}'s request to join group {c.group}" + _reason(c)
    ),
    AuditAction.CREATE_GROUP_JOIN_REQUEST: lambda c: (
        f"requested to join group {c.group}"
    ),
    AuditAction.CANCEL_GROUP_JOIN_REQUEST: lambda c: (
        f"cancelled their request to join group {c.group}"
    ),
    # Venues
    AuditAction.CREATE_VENUE: _on_resource("created", "venue"),
    AuditAction.UPDATE_VENUE: _on_resource("updated", "venue"),
    AuditAction.RESTORE_VENUE: _on_resource("restored", "venue"),
    AuditAction.SOFT_DELETE_VENUE: _on_resource("deleted", "venue"),
    # --- System preferences (#525) ---
    AuditAction.UPDATE_SYSTEM_PREFERENCE: lambda c: (
        f"set system preference {c.detail('key') or c.resource}"
    ),
    # --- Public staff listing (#332) ---
    AuditAction.UPDATE_STAFF_LISTING: _on_target("curated {t}'s public staff listing"),
    AuditAction.DELETE_STAFF_LISTING: _on_target("removed {t}'s public staff listing"),
    # --- Event marketing (#410) ---
    AuditAction.UPDATE_EVENT_MARKETING: _on_resource(
        "updated the marketing of", "event"
    ),
    AuditAction.DELETE_EVENT_MARKETING: _on_resource(
        "removed the marketing of", "event"
    ),
    # --- Inquiries (#407) ---
    AuditAction.HANDLE_INQUIRY: _on_resource("handled", "inquiry"),
    AuditAction.DELETE_INQUIRY: _on_resource("deleted", "inquiry"),
    AuditAction.WIPEOUT_VENUE: _on_resource("permanently deleted", "venue"),
    # Media
    AuditAction.UPLOAD_MEDIA_V2: lambda c: (
        f"uploaded media {c.detail('filename') or ''}".rstrip()
    ),
    AuditAction.UPDATE_MEDIA_V2: _plain("updated media access settings"),
    AuditAction.SOFT_DELETE_MEDIA_V2: lambda c: (
        f"deleted media {c.detail('filename') or ''}".rstrip()
    ),
    AuditAction.RESTORE_MEDIA_V2: _plain("restored a media item"),
    AuditAction.HARD_DELETE_MEDIA_V2: lambda c: (
        f"permanently deleted media {c.detail('filename') or ''}".rstrip()
    ),
    AuditAction.ENCRYPT_MEDIA_V2: lambda c: (
        f"encrypted media {c.detail('filename') or ''}".rstrip()
    ),
    # Media links
    AuditAction.CREATE_USER_MEDIA_LINK: _media_link(
        "linked media to a {o} (tag: {tag})"
    ),
    AuditAction.CREATE_EVENT_MEDIA_LINK: _media_link(
        "linked media to a {o} (tag: {tag})"
    ),
    AuditAction.CREATE_GROUP_MEDIA_LINK: _media_link(
        "linked media to a {o} (tag: {tag})"
    ),
    AuditAction.CREATE_VENUE_MEDIA_LINK: _media_link(
        "linked media to a {o} (tag: {tag})"
    ),
    AuditAction.UPDATE_USER_MEDIA_LINK: _media_link("updated a {o} media link"),
    AuditAction.UPDATE_EVENT_MEDIA_LINK: _media_link("updated a {o} media link"),
    AuditAction.UPDATE_GROUP_MEDIA_LINK: _media_link("updated a {o} media link"),
    AuditAction.UPDATE_VENUE_MEDIA_LINK: _media_link("updated a {o} media link"),
    AuditAction.DELETE_USER_MEDIA_LINK: _media_link(
        "removed a media link from a {o} (tag: {tag})"
    ),
    AuditAction.DELETE_EVENT_MEDIA_LINK: _media_link(
        "removed a media link from a {o} (tag: {tag})"
    ),
    AuditAction.DELETE_GROUP_MEDIA_LINK: _media_link(
        "removed a media link from a {o} (tag: {tag})"
    ),
    AuditAction.DELETE_VENUE_MEDIA_LINK: _media_link(
        "removed a media link from a {o} (tag: {tag})"
    ),
    AuditAction.DELETE_USER_MEDIA_TAG: _media_link(
        "removed the {tag} media tag from a {o}"
    ),
    AuditAction.DELETE_EVENT_MEDIA_TAG: _media_link(
        "removed the {tag} media tag from a {o}"
    ),
    AuditAction.DELETE_GROUP_MEDIA_TAG: _media_link(
        "removed the {tag} media tag from a {o}"
    ),
    AuditAction.DELETE_VENUE_MEDIA_TAG: _media_link(
        "removed the {tag} media tag from a {o}"
    ),
    # Broadcasts
    AuditAction.CREATE_BROADCAST: lambda c: (
        f"sent a broadcast to {c.details.get('recipientCount', 'its')} recipient(s)"
    ),
    AuditAction.REVOKE_BROADCAST: _plain("revoked a broadcast"),
    # Notifications
    AuditAction.CREATE_NOTIFICATION: _on_target("sent a notification to {t}"),
    AuditAction.DELETE_NOTIFICATION: _plain("deleted a notification"),
    # Credit system
    AuditAction.OPEN_CREDIT_ACCOUNT: _credit_account(
        "opened credit account {a} for {t}"
    ),
    AuditAction.EXTEND_CREDIT_ACCOUNT: _credit_account(
        "extended the validity of credit account {a} for {t}"
    ),
    AuditAction.REVERSE_CREDIT_GRANT: _credit_account(
        "reversed a credit grant on account {a} for {t}"
    ),
    AuditAction.TRANSFER_CREDIT_ACCOUNT: _credit_account(
        "closed credit account {a} for {t} and transferred the balance"
    ),
    AuditAction.REOPEN_CREDIT_ACCOUNT: _credit_account(
        "reopened closed credit account {a} for {t} to take a session refund"
    ),
    AuditAction.CREDIT_DEDUCTED: _enrollment("charged {t} for a session on {r}"),
    AuditAction.CREDIT_REFUNDED: _enrollment("refunded {t} for a session on {r}"),
    AuditAction.CREDIT_RELEASED: _enrollment(
        "released {t}'s credit bound to ended programme {r}"
    ),
    # Evaluations
    AuditAction.CREATE_EVALUATION: _on_target("created an evaluation for {t}"),
    AuditAction.UPDATE_EVALUATION: _on_resource("updated", "evaluation"),
    AuditAction.SAVE_EVALUATION: _on_resource("saved", "evaluation"),
    AuditAction.PUBLISH_EVALUATION: _on_target("published an evaluation for {t}"),
    AuditAction.UNPUBLISH_EVALUATION: _on_target("withdrew an evaluation for {t}"),
    AuditAction.REVERT_EVALUATION: _on_resource("reverted", "evaluation"),
    AuditAction.TRANSFER_EVALUATION: _on_resource("transferred", "evaluation"),
    AuditAction.DELETE_EVALUATION: _on_resource("deleted", "evaluation"),
    AuditAction.RESTORE_EVALUATION: _on_resource("restored", "evaluation"),
    AuditAction.HARD_DELETE_EVALUATION: _on_resource(
        "permanently deleted", "evaluation"
    ),
    AuditAction.CREATE_EVALUATION_TEMPLATE: _on_resource(
        "created", "evaluation template"
    ),
    AuditAction.UPDATE_EVALUATION_TEMPLATE: _on_resource(
        "updated", "evaluation template"
    ),
    AuditAction.DELETE_EVALUATION_TEMPLATE: _on_resource(
        "deleted", "evaluation template"
    ),
    AuditAction.RESTORE_EVALUATION_TEMPLATE: _on_resource(
        "restored", "evaluation template"
    ),
    AuditAction.HARD_DELETE_EVALUATION_TEMPLATE: _on_resource(
        "permanently deleted", "evaluation template"
    ),
    AuditAction.CREATE_EVALUATION_MEDIA: _media_link("attached media to {o} {tag}"),
    AuditAction.UPDATE_EVALUATION_MEDIA: _media_link("updated media on {o} {tag}"),
    AuditAction.DELETE_EVALUATION_MEDIA: _media_link("removed media from {o} {tag}"),
    AuditAction.DELETE_EVALUATION_MEDIA_TAG: _media_link(
        "removed the {tag} media from {o}"
    ),
}

TEMPLATES: dict[str, dict[AuditAction, Builder]] = {"en": _EN}


def _compose(ctx: SummaryContext, predicate: str) -> str:
    sentence = f"{ctx.actor} {predicate} on {ctx.when}"
    if ctx.ip:
        sentence += f" from {ctx.ip}"
    return sentence + "."


def _generic_predicate(action: str) -> str:
    return f"performed action '{action}'"


def render_summaries(
    action: str,
    ctx: SummaryContext,
    languages: tuple[str, ...] = SUPPORTED_LANGUAGES,
) -> dict[str, str]:
    """Render the ``{lang: sentence}`` summary map for one row.

    Unknown actions use the generic fallback (in the default language only).
    A known action missing a template in a non-default language is skipped.
    """
    try:
        member: AuditAction | None = AuditAction(action)
    except ValueError:
        member = None

    out: dict[str, str] = {}
    for lang in languages:
        if member is None:
            if lang == DEFAULT_LANGUAGE:
                out[lang] = _compose(ctx, _generic_predicate(action))
            continue
        builder = TEMPLATES.get(lang, {}).get(member)
        if builder is None:
            continue
        out[lang] = _compose(ctx, builder(ctx))
    return out
