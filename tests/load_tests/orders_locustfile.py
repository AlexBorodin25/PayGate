import os
from random import choice

from locust import HttpUser, between, task


class OrdersApiUser(HttpUser):
    wait_time = between(1.0, 3.0)

    def on_start(self) -> None:
        self.api_key = os.environ["ORDERS_API_KEY"]

    @task(5)
    def list_orders_first_page(self) -> None:
        self.client.get(
            "/orders?page=1&page_size=10&include_total=false",
            headers={"X-API-Key": self.api_key},
            name="/orders first page",
        )

    @task(3)
    def list_orders_with_filters(self) -> None:
        status = choice(["pending", "paid", "checkout_failed"])
        fulfillment_status = choice(
            ["pending", "processing", "fulfilled", "payment_review", "failed"]
        )

        self.client.get(
            (
                "/orders?page=1&page_size=10&include_total=false"
                f"&status={status}"
                f"&fulfillment_status={fulfillment_status}"
            ),
            headers={"X-API-Key": self.api_key},
            name="/orders filtered",
        )

    @task(2)
    def list_orders_sorted(self) -> None:
        sort_by = choice(
            ["created_at", "id", "amount", "status", "fulfillment_status", "product_id"]
        )
        sort_direction = choice(["asc", "desc"])

        self.client.get(
            (
                "/orders?page=1&page_size=10&include_total=false"
                f"&sort_by={sort_by}"
                f"&sort_direction={sort_direction}"
            ),
            headers={"X-API-Key": self.api_key},
            name="/orders sorted",
        )

    @task(1)
    def list_later_page(self) -> None:
        page = choice([2, 5, 10, 25, 50])

        self.client.get(
            f"/orders?page={page}&page_size=10&include_total=false",
            headers={"X-API-Key": self.api_key},
            name="/orders later page",
        )
