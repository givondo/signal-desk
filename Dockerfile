# Signal Desk - works on Render, Koyeb, Hugging Face Spaces (set app_port), etc.
FROM python:3.12-slim
WORKDIR /app
COPY xauusd_trader.py precifarm_analyst.py dashboard.html trading_tracker.py tracker.html ./
COPY trading_os ./trading_os
ENV PORT=8899
EXPOSE 8899
CMD ["python", "xauusd_trader.py"]
