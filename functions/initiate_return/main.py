import functions_framework
import json
import uuid
from datetime import datetime

@functions_framework.http
def initiate_return(request):
    data = request.get_json(silent=True) or {}
    order_id = data.get("order_id", "").upper()
    reason = data.get("reason", "Customer requested return")

    if not order_id:
        return json.dumps({"error": "order_id required"}), 400, \
            {"Content-Type": "application/json"}

    # Only delivered/shipped orders are returnable
    ORDERS = {
        "ORD-001": {"status": "shipped",    "product_name": "Wireless Headphones"},
        "ORD-002": {"status": "processing", "product_name": "Laptop Stand"},
        "ORD-003": {"status": "delivered",  "product_name": "USB-C Hub"},
    }

    if order_id not in ORDERS:
        return json.dumps({"error": f"Order {order_id} not found"}), 404, \
            {"Content-Type": "application/json"}

    order = ORDERS[order_id]
    if order["status"] not in ["delivered", "shipped"]:
        return json.dumps({
            "error": f"Order cannot be returned. Status: {order['status']}. "
                     "Only delivered or shipped orders are eligible."
        }), 400, {"Content-Type": "application/json"}

    return_id = f"RET-{str(uuid.uuid4())[:8].upper()}"
    return json.dumps({
        "success": True,
        "return_id": return_id,
        "order_id": order_id,
        "reason": reason,
        "return_label_url": f"https://returns.shopnova.com/label/{return_id}",
        "message": f"Return {return_id} initiated. Use the label to ship "
                   "the item back. Refund in 5-7 business days after receipt.",
        "initiated_at": datetime.utcnow().isoformat()
    }), 200, {"Content-Type": "application/json"}
