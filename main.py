from joshua import Joshua
import logging

if __name__ == "__main__":
    print("Start running...")

    # logging.basicConfig(level=logging.DEBUG)
    try:
        app = Joshua(
            symbols=["BTCUSDT", "ETHUSDT"],
            interval="5m",
            leverage=10,
            size=30,
            rsi_window=6,
            is_testnet=True,
        )
        app.run()
    except KeyboardInterrupt:
        app.close()
