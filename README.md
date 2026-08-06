## 🔗 LIVE APP:

👉 [View the Stock Watchlist App](https://databricks-app-day-1-7474657899306491.aws.databricksapps.com/)

> **Note:** Access to the live application may require Databricks authentication.

![Uploading Screenshot 2026-08-05 223407.png…]()



# Databricks Lakebase Stock Watchlist

This is a stock watchlist application I built as part of ** Databricks AI Bootcamp**.

The main goal of the project was to get hands-on experience with **Databricks Apps and Lakebase** and understand how a frontend, Python backend, external API, and database can work together in one application.

I used the bootcamp project as the starting point and built a stock watchlist where a user can add stocks, see their latest price, and remove stocks they no longer want to track.

## What the app does

The app allows a user to:

- Enter a stock ticker such as AAPL, MSFT, or TSLA
- Get the latest available stock price using the Massive API
- Add the stock to a personal watchlist
- View the stocks already saved in the watchlist
- See when the stock price was last updated
- Remove a stock from the watchlist

Each watchlist is associated with the user's email, so the data can be stored separately for different users.

## How it works

The application has a few different pieces working together:

**Frontend → Flask backend → Massive API → Lakebase**

When a user enters a ticker symbol, the frontend sends the request to the Flask application.

The backend then calls the Massive API to get the stock price and stores the result in Lakebase Postgres.

The saved records are then retrieved from Lakebase and displayed back in the watchlist.

## Technologies I used

- Databricks Apps
- Databricks Lakebase
- Python
- Flask
- PostgreSQL
- Massive API
- HTML
- CSS
- JavaScript
- GitHub

## Project structure

```text
databricks-lakebase-stock-watchlist/
│
├── templates/
│   └── index.html
│
├── app.py
├── app.yaml
├── lakebase.py
├── massive_client.py
├── setup_secrets.py
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
