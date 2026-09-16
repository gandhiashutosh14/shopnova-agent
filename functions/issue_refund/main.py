import functions_framework
import json
import uuid
from datetime import datetime

REFUND_POLICY = {
    "requires_approval_above": 200.0,
    "max_amount": 500.0
}

@functions_framework.http
def issue_refund(request):
    data = request.get_json(silent=True) or {}
    order_id = data.get("order_id", "").upper()
    approved = data.get("approved", False)

    if not order_id:
        return json.dumps({"error": "order_id required"}), 400, \
            {"Content-Type": "application/json"}

    ORDERS = {
        "ORD-001": {"product_name": "Wireless Headphones", "total_amount": 79.99},
        "ORD-002": {"product_name": "Laptop Stand",        "total_amount": 51.25},
        "ORD-003": {"product_name": "USB-C Hub",           "total_amount": 34.99},
    }

    if order_id not in ORDERS:
        return json.dumps({"error": f"Order {order_id} not found"}), 404, \
            {"Content-Type": "application/json"}

    order = ORDERS[order_id]
    amount = order["total_amount"]

    # GUARDRAIL: Large refunds need human approval before processing
    if amount > REFUND_POLICY["requires_approval_above"] and not approved:
        return json.dumps({
            "requires_approval": True,
            "escalation_needed": True,
            "order_id": order_id,
            "refund_amount": amount,
            "message": f"Refund of ${amount} exceeds the ${REFUND_POLICY['requires_approval_above']}"
                       " policy threshold. Human approval required."
        }), 200, {"Content-Type": "application/json"}

    refund_id = f"REF-{str(uuid.uuid4())[:8].upper()}"
    return json.dumps({
        "success": True,
        "refund_id": refund_id,
        "order_id": order_id,
        "refund_amount": amount,
        "message": f"Refund of ${amount:.2f} processed. ID: {refund_id}. "
                   "Credit appears in 3-5 business days.",
        "processed_at": datetime.utcnow().isoformat()
    }), 200, {"Content-Type": "application/json"}
