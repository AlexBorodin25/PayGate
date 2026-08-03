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


URL:
https://paygate-b2ll.onrender.com/

To run the app locally use "uvicorn app.main:app --reload"

Can open:
http://localhost:8000
http://localhost:8000/products
http://localhost:8000/docs
http://localhost:8000/health


To run a manual checkout test use:

$response = Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/checkout" `
  -ContentType "application/json" `
  -Body '{"product_id":"speaker"}'

$response
Start-Process $response.checkout_url

Use card 4242 4242 4242 4242.

To view /orders use:
Invoke-RestMethod `
  -Uri "https://paygate-b2ll.onrender.com/orders" `
  -Headers @{ "X-API-Key" = "your-secret-key" }

To test failed checkout:

Temporarily set an invalid Stripe secret key in '.env' or click back/cancel on the Stripe checkout page.

To run Locust load_orders test use this:

$env:ORDERS_API_KEY="your-orders-api-key"
$env:LOAD_TEST_HOST="http://127.0.0.1:8000"

.\scripts\run_orders_load_tests.ps1