"""Batch operations: merging several extraction batches into one."""
from django.db import transaction as db_transaction
from django.utils import timezone

from core.models import ExtractionBatch, SMSMessage, Transaction


class MergeError(Exception):
    pass


def merge_batches(user, batches) -> ExtractionBatch:
    """Move every SMS and transaction from ``batches`` into one new batch.

    * Source batches are not deleted: they are marked "merged", keep their
      historical counts and point to the new batch (audit trail).
    * Messages are renumbered in chronological order (oldest batch first).
    * Duplicate flags are preserved; counts are recalculated for the new batch.
    * Reports already generated from a source batch keep their link to it.
    """
    with db_transaction.atomic():
        ids = [b.pk for b in batches]
        locked = list(
            ExtractionBatch.objects.select_for_update().filter(pk__in=ids).order_by("created_at", "id")
        )
        if len(locked) < 2:
            raise MergeError("Select at least two batches to merge.")
        already = [b.short_uid for b in locked if b.is_merged]
        if already:
            raise MergeError(f"Already merged and empty: {', '.join(already)}.")
        if b_processing := [b.short_uid for b in locked if b.state == ExtractionBatch.State.PROCESSING]:
            raise MergeError(f"Still processing: {', '.join(b_processing)}.")

        owners = {b.created_by_id for b in locked}
        owner = locked[0].created_by if len(owners) == 1 else user
        hints = {b.source_hint for b in locked}
        merged = ExtractionBatch.objects.create(
            created_by=owner,
            input_method=ExtractionBatch.Method.MERGED,
            source_hint=hints.pop() if len(hints) == 1 else "AUTO",
            filename=("Merged: " + ", ".join(b.short_uid for b in locked))[:255],
            defaults={"merged_from": [str(b.uid) for b in locked], "merged_by": user.email},
        )

        position = 0
        for batch in locked:
            for sms in SMSMessage.objects.filter(batch=batch).order_by("position", "id"):
                position += 1
                SMSMessage.objects.filter(pk=sms.pk).update(batch=merged, position=position)
            Transaction.objects.filter(batch=batch).update(batch=merged, updated_at=timezone.now())
            batch.state = ExtractionBatch.State.MERGED
            batch.merged_into = merged
            batch.merged_at = timezone.now()
            batch.save(update_fields=["state", "merged_into", "merged_at", "updated_at"])

        merged.refresh_counts()
    return merged
