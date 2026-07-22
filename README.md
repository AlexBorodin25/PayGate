# PayGate

PayGate is a small FastAPI application that sells a small catalogue of digital products through Stripe Checkout.

# Features

- List available products with name, description, price, currency, and quantity
- Create Stripe Checkout Sessions from a server-side product id
- Store pending orders before redirecting to Stripe Checkout
- Verify Stripe webhook signatures before changing payment state
- Mark orders as paid only from verified `checkout.session.completed` events
- Prevent deleted products from appearing in the public product list
- Prevent checkout for deleted or out-of-stock products
- Reserve stock atomically before checkout
- Use Alembic migrations for database schema changes
- Load configuration from environment variables
- Run linting, formatting, type checks, and tests

To run the app use "uvicorn app.main:app --reload"

Can open:
http://localhost:8000/health
http://localhost:8000/products


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
  -Uri "https://your-app.onrender.com/orders" `
  -Headers @{ "X-API-Key" = "your-secret-key" }