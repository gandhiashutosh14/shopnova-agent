import functions_framework
import json

# In production: replace with actual BigQuery/database query
SAMPLE_ORDERS = {
    "ORD-001": {
        "order_id": "ORD-001",
        "customer_id": "CUST-123",
        "product_name": "Wireless Headphones",
        "quantity": 1,
        "total_amount": 79.99,
        "status": "shipped",
        "tracking_number": "TRK9876543210",
        "estimated_delivery": "2026-06-10"
    },
    "ORD-002": {
        "order_id": "ORD-002",
        "customer_id": "CUST-456",
        "product_name": "Laptop Stand",
        "quantity": 2,
        "total_amount": 51.25,
        "status": "processing",
        "tracking_number": None,
        "estimated_delivery": "2026-06-12"
    },
    "ORD-003": {
        "order_id": "ORD-003",
        "customer_id": "CUST-789",
        "product_name": "USB-C Hub",
        "quantity": 1,
        "total_amount": 34.99,
        "status": "delivered",
        "tracking_number": "TRK1234567890",
        "estimated_delivery": "2026-06-05"
    },
}

@functions_framework.http
def get_order_status(request):
    data = request.get_json(silent=True) or {}
    order_id = data.get("order_id", "").upper()

    if not order_id:
        return json.dumps({"error": "order_id is required"}), 400, \
            {"Content-Type": "application/json"}

    if order_id in SAMPLE_ORDERS:
        order = SAMPLE_ORDERS[order_id]
        msg = f"Order {order_id} for '{order['product_name']}' " \
              f"is currently {order['status']}."
        if order["status"] == "shipped":
            msg += f" Tracking: {order['tracking_number']}. " \
                   f"ETA: {order['estimated_delivery']}."
        elif order["status"] == "delivered":
            msg += f" Delivered on {order['estimated_delivery']}."
        elif order["status"] == "processing":
            msg += f" Expected to ship by {order['estimated_delivery']}."

        return json.dumps({
            "order_id": order_id,
            "status": order["status"],
            "message": msg,
            "details": order
        }), 200, {"Content-Type": "application/json"}
    else:
        return json.dumps({
            "error": f"Order {order_id} not found."
        }), 404, {"Content-Type": "application/json"}
