# sdk-broad: A Python Simulation of a Cross-Chain Bridge Event Listener

This repository contains a Python-based simulation of a critical component in a cross-chain bridge system: an event listener (also known as an oracle or relayer). This application is designed to monitor a smart contract on a source blockchain (EVM-compatible), detect specific events, and relay that data to a destination chain's API endpoint.

This project serves as an architectural prototype to demonstrate a robust, modular, and fault-tolerant design suitable for real-world decentralized applications.

## Concept

A cross-chain bridge allows users to transfer assets or data from one blockchain to another. A common mechanism involves a 'lock-and-mint' or 'burn-and-release' pattern:

1.  **Lock on Source Chain**: A user sends tokens to a bridge smart contract on the source chain (e.g., Ethereum). The contract locks these tokens and emits an event (e.g., `TokensLocked`) containing details of the transaction (sender, recipient, amount, destination chain).
2.  **Relay Event**: Off-chain services, called listeners or oracles, constantly monitor the source chain for these events. Upon detecting a `TokensLocked` event, they validate it.
3.  **Mint on Destination Chain**: The listener then submits a transaction to a corresponding contract on the destination chain (e.g., Polygon), providing proof of the lock event. This destination contract then mints an equivalent amount of 'wrapped' tokens for the recipient.

This application simulates the **second step** of this process. It listens for `TokensLocked` events and securely relays the information to a mock API that represents the destination chain's entry point.

### Example Event

The listener is configured to scan for events with a specific signature on the bridge contract. For example:

```solidity
// Example Solidity event the listener is designed to detect
event TokensLocked(
    address indexed sender,
    address indexed recipient,
    uint256 amount,
    uint256 destinationChainId
);
```

## Architecture

The application is designed with a clear separation of concerns, dividing the logic into several distinct classes:

-   `Config`
    -   **Responsibility**: Manages all configuration parameters for the application.
    -   **Details**: It loads settings from environment variables (`.env` file) for better security and deployment flexibility. It also validates that essential parameters like RPC URLs and contract addresses are present and correctly formatted before the application starts.

-   `BlockchainConnector`
    -   **Responsibility**: Handles all direct communication with the source blockchain's RPC node.
    -   **Details**: It wraps the `web3.py` library to manage the connection. This class is responsible for establishing and verifying the connection, creating a contract instance, and querying for blockchain data like the latest block number.

-   `EventScanner`
    -   **Responsibility**: The core logic for scanning the blockchain for relevant events.
    -   **Details**: It maintains its own state, tracking the last block number it has scanned. To prevent issues with blockchain reorganizations (reorgs), it only scans blocks that have received a certain number of confirmations. It calculates block ranges for scanning and uses the `BlockchainConnector` to query for event logs from the bridge contract.

-   `CrossChainRelayer`
    -   **Responsibility**: Simulates the action of relaying the event data to the destination chain.
    -   **Details**: In this simulation, instead of signing and broadcasting a transaction on another chain, this class makes an authenticated HTTP POST request to a mock API endpoint. It includes a robust retry mechanism with exponential backoff to handle transient network failures or API downtime.

-   `BridgeOrchestrator`
    -   **Responsibility**: The main entry point and orchestrator of the entire process.
    -   **Details**: It initializes and wires together all the other components. It contains the main application loop, which periodically triggers the `EventScanner`, processes the events found, and passes them to the `CrossChainRelayer`. It also handles top-level error management and graceful shutdown.

## How it Works

The application operates in a continuous loop, performing the following steps:

1.  **Initialization**: The `BridgeOrchestrator` is instantiated. It loads the configuration, establishes a connection to the source chain RPC via the `BlockchainConnector`, and prepares the `EventScanner` and `CrossChainRelayer`.
2.  **Calculate Scan Range**: In each loop iteration, the `EventScanner` determines the range of blocks to scan. It starts from `last_scanned_block + 1` and ends at `latest_block - BLOCK_CONFIRMATIONS`.
3.  **Fetch Logs**: It uses `web3.py` to query the RPC node for event logs matching the `TokensLocked` event signature from the specified bridge contract within the calculated block range.
4.  **Process Events**: If any events are found, the `BridgeOrchestrator` iterates through them.
5.  **Prevent Duplicates**: It checks a local cache (`processed_txs`) to ensure the event's transaction hash has not already been processed, preventing duplicate relays.
6.  **Relay Data**: For each new, valid event, the orchestrator invokes the `CrossChainRelayer`. The relayer formats the event data into a JSON payload and sends it to the configured destination API endpoint.
7.  **Handle Relay Failure**: If the API call fails, the error is logged. The event is *not* marked as processed, ensuring that the system will automatically retry relaying it in the next scan cycle.
8.  **Update State**: After a block range is scanned successfully (regardless of whether events were found), the `EventScanner` updates its `last_scanned_block` state.
9.  **Wait**: The orchestrator pauses for a configured interval (`POLL_INTERVAL_SECONDS`) before starting the next cycle.

## Usage Guide

### 1. Prerequisites

-   Python 3.8+
-   An RPC endpoint URL for an EVM-compatible blockchain (e.g., from Infura, Alchemy, or a local node).

### 2. Installation

Clone the repository and install the required Python packages. It is highly recommended to use a virtual environment.

```bash
git clone https://github.com/your-username/sdk-broad.git
cd sdk-broad

# Create and activate a virtual environment (optional but recommended)
python3 -m venv venv
source venv/bin/activate  # On Windows, use `venv\Scripts\activate`

# Install dependencies
pip install -r requirements.txt
```

### 3. Configuration

Create a `.env` file in the root of the project directory. This file will store your configuration and sensitive credentials.

```ini
# .env example file

# RPC URL for the source EVM-compatible blockchain (e.g., Ethereum Sepolia)
SOURCE_RPC_URL="https://sepolia.infura.io/v3/YOUR_INFURA_PROJECT_ID"

# The address of the bridge smart contract to monitor
BRIDGE_CONTRACT_ADDRESS="0x1234567890123456789012345678901234567890"

# Block number to start scanning from. Use a recent block for faster startup, or 0 for the entire history.
START_BLOCK="1000000"

# (Optional) The API endpoint of the destination chain to relay events to.
DESTINATION_API_ENDPOINT="https://api.mock-destination-chain.com/submit"

# (Optional) API key for authenticating with the destination endpoint.
API_KEY="your-secret-api-key"
```

Replace the placeholder values with your actual data.

### 4. Running the Listener

Create a main execution script, for example `main.py`, to initialize and run the `BridgeOrchestrator`.

```python
# main.py
import logging
from orchestrator import BridgeOrchestrator # Assuming your orchestrator is in 'orchestrator.py'

# Configure basic logging to see the output
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - (%(module)s) - %(message)s'
)

if __name__ == "__main__":
    try:
        orchestrator = BridgeOrchestrator()
        orchestrator.run()
    except KeyboardInterrupt:
        logging.info("Shutting down gracefully...")
    except Exception as e:
        logging.critical(f"An unexpected error occurred: {e}", exc_info=True)

```

Execute the script from your terminal:

```bash
python main.py
```

The listener will start, and you will see log output in your console similar to this:

```
2023-10-27 15:30:00 - [INFO] - (Config) - Configuration loaded and validated successfully.
2023-10-27 15:30:01 - [INFO] - (BlockchainConnector) - Successfully connected to RPC node. Chain ID: 11155111
2023-10-27 15:30:01 - [INFO] - (BridgeOrchestrator) - Starting cross-chain bridge event listener...
2023-10-27 15:30:02 - [INFO] - (EventScanner) - Scanning for 'TokensLocked' events from block 1000000 to 1004550.
2023-10-27 15:30:05 - [INFO] - (EventScanner) - Found 1 new event(s) in range.
2023-10-27 15:30:05 - [INFO] - (BridgeOrchestrator) - Processing event from Tx: 0xabc...def in block 1001234
2023-10-27 15:30:05 - [INFO] - (CrossChainRelayer) - Relaying event for Tx: 0xabc...def
2023-10-27 15:30:06 - [INFO] - (CrossChainRelayer) - Successfully relayed event. API response: {'status': 'success', 'tx_hash': '0x987...654'}
2023-10-27 15:30:06 - [INFO] - (BridgeOrchestrator) - Scan cycle complete. Waiting for 15 seconds...
```