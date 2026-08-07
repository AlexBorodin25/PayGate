# PayGate

PayGate is a small FastAPI application that sells a small catalogue of digital products through Stripe Checkout.

It demonstrates server-side pricing, hosted checkout, verified webhook payment confirmation, async fulfillment, 
protected order visibility, Postgres persistence, Alembic migrations, Docker packaging, and CI quality gates

# Features

Product catalogue with name, description, price, currency, and stock quantity
Stripe Checkout Session creation from a server-side product id
Server-authoritative pricing; the browser never submits a trusted price
Verified Stripe webhook payment confirmation
Payment reconciliation using session id, amount, currency, and livemode
Duplicate webhook protection with recorded Stripe event ids
Race-safe fulfillment claim
Protected order status endpoint
Alembic-managed Postgres schema
Dockerized runtime with a non-root user
GitHub Actions quality pipeline
Stock reservation during checkout so the first successful payer wins
Late payment handling with fulfillment or manual review
Orders UI with API key access, pagination, filters, sorting, order details, and retry fulfillment
Needs-attention filters for failed fulfillment and payment review orders
Product page pagination
Product images with fallback placeholders
Locust load testing for Orders API high-load behavior

Fulfillment flow:

Stripe confirms payment by webhook. PayGate verifies and reconciles the event, marks the order paid, 
then publishes a QStash job. QStash calls the internal fulfillment endpoint with a signed request and 
forwarded internal secret. The internal endpoint verifies the QStash signature, rejects replayed message ids, 
and runs the same atomic fulfillment claim.

URL:
https://paygate-b2ll.onrender.com/

Main UI pages:
https://paygate-b2ll.onrender.com/
https://paygate-b2ll.onrender.com/orders-ui
https://paygate-b2ll.onrender.com/docs
https://paygate-b2ll.onrender.com/health

To open the Orders UI in a browser:
https://paygate-b2ll.onrender.com/orders-ui

Enter the Orders API key in the page to load orders. The UI supports pagination, filters, sorting, order details, needs-attention filters, and manual fulfillment retry for eligible paid orders.

To run the app locally use "uvicorn app.main:app --reload"

Can open:
http://localhost:8000
http://localhost:8000/products
http://localhost:8000/docs
http://localhost:8000/health
http://localhost:8000/orders


For checkout use card:
4242 4242 4242 4242.

To test failed checkout:

Temporarily set an invalid Stripe secret key in '.env' or click back/cancel on the Stripe checkout page.

To run Locust load_orders test use this:

$env:ORDERS_API_KEY="your-orders-api-key"
$env:LOAD_TEST_HOST="http://127.0.0.1:8000"

.\scripts\run_orders_load_tests.ps1