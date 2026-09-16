import functions_framework
import json
import uuid
from datetime import datetime, timezone

REFUND_POLICY = {
    "requires_approval_above": 200.0,
    "max_amount": 500.0,
}


@functions_framework.http
def issue_refund(request):
    """Issue a sample refund or request human approval.

    The model-facing tool intentionally accepts no approval flag. Human approval
    is a control-plane concern: an LLM cannot authorize its own high-value refund
    by adding an argument to a function call.
    """
    data = request.get_json(silent=True) or {}
    order_id = data.get("order_id", "").upper()

    if not order_id:
        return json.dumps({"error": "order_id required"}), 400, {
            "Content-Type": "application/json"
        }

    ORDERS = {
        "ORD-001": {"product_name": "Wireless Headphones", "total_amount": 79.99},
        "ORD-002": {"product_name": "Laptop Stand", "total_amount": 51.25},
        "ORD-003": {"product_name": "USB-C Hub", "total_amount": 34.99},
    }

    if order_id not in ORDERS:
        return json.dumps({"error": f"Order {order_id} not found"}), 404, {
            "Content-Type": "application/json"
        }

    order = ORDERS[order_id]
    amount = order["total_amount"]

    if amount > REFUND_POLICY["max_amount"]:
        return json.dumps({
            "error": "refund exceeds maximum automated refund policy",
            "order_id": order_id,
            "refund_amount": amount,
            "escalation_needed": True,
        }), 200, {"Content-Type": "application/json"}

    # SECURITY BOUNDARY: large refunds always stop here. The function does not
    # consume any model-supplied "approved" value; approval must happen in a
    # separate trusted workflow before a production refund service is invoked.
    if amount > REFUND_POLICY["requires_approval_above"]:
        return json.dumps({
            "requires_approval": True,
            "escalation_needed": True,
            "order_id": order_id,
            "refund_amount": amount,
            "message": (
                f"Refund of ${amount:.2f} exceeds the "
                f"${REFUND_POLICY['requires_approval_above']:.2f} policy threshold. "
                "Human approval required."
            ),
        }), 200, {"Content-Type": "application/json"}

    refund_id = f"REF-{str(uuid.uuid4())[:8].upper()}"
    return json.dumps({
        "success": True,
        "refund_id": refund_id,
        "order_id": order_id,
        "refund_amount": amount,
        "message": (
            f"Refund of ${amount:.2f} processed. ID: {refund_id}. "
            "Credit appears in 3-5 business days."
        ),
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }), 200, {"Content-Type": "application/json"}
