# Signal Desk - works on Render, Koyeb, Hugging Face Spaces (set app_port), etc.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY xauusd_trader.py precifarm_analyst.py claude_desk.py dashboard.html ./
COPY docs/PRECIFARM_DESK_ANALYST.md docs/
ENV PORT=8899
EXPOSE 8899
CMD ["python", "xauusd_trader.py"]
