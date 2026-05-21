import uvicorn


if __name__ == "__main__":
    uvicorn.run("cc2_dash.main:app", host="0.0.0.0", port=8088, reload=False)
