import os
import time
import json
import logging
from typing import Dict, Any, Generator, Optional

import requests
from web3 import Web3
from web3.contract import Contract
from web3.datastructures import AttributeDict
from requests.adapters import HTTPAdapter, Retry
from dotenv import load_dotenv

# --- Basic Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - (%(name)s) - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


class Config:
    """
    Configuration class to manage settings for the bridge listener.
    Loads parameters from environment variables for security and flexibility.
    """
    def __init__(self):
        load_dotenv()
        self.SOURCE_RPC_URL: Optional[str] = os.getenv('SOURCE_RPC_URL')
        self.BRIDGE_CONTRACT_ADDRESS: Optional[str] = os.getenv('BRIDGE_CONTRACT_ADDRESS')
        self.DESTINATION_API_ENDPOINT: Optional[str] = os.getenv('DESTINATION_API_ENDPOINT', 'https://api.mock-destination-chain.com/submit')
        self.API_KEY: Optional[str] = os.getenv('API_KEY', 'default-secret-key')
        self.START_BLOCK: int = int(os.getenv('START_BLOCK', '0'))
        self.POLL_INTERVAL_SECONDS: int = int(os.getenv('POLL_INTERVAL_SECONDS', 15))
        # Number of blocks to wait for finality, reduces risk of processing reorged blocks.
        self.BLOCK_CONFIRMATIONS: int = int(os.getenv('BLOCK_CONFIRMATIONS', 12))

        self.validate()

    def validate(self):
        """Validates that essential configuration variables are set."""
        if not self.SOURCE_RPC_URL:
            raise ValueError("SOURCE_RPC_URL environment variable not set.")
        if not self.BRIDGE_CONTRACT_ADDRESS or not Web3.is_address(self.BRIDGE_CONTRACT_ADDRESS):
            raise ValueError("BRIDGE_CONTRACT_ADDRESS is not set or is not a valid address.")
        logging.info("Configuration loaded and validated successfully.")


class BlockchainConnector:
    """
    Manages the connection to a blockchain node via Web3.py.
    Encapsulates the Web3 instance and provides utility methods for blockchain interaction.
    """
    def __init__(self, rpc_url: str):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not self.w3.is_connected():
            self.logger.error(f"Failed to connect to blockchain node at {rpc_url}")
            raise ConnectionError("Unable to connect to the specified RPC URL.")
        self.logger.info(f"Successfully connected to RPC node. Chain ID: {self.w3.eth.chain_id}")

    def get_contract(self, address: str, abi: list) -> Contract:
        """Returns a Web3 contract instance ready for interaction."""
        checksum_address = Web3.to_checksum_address(address)
        return self.w3.eth.contract(address=checksum_address, abi=abi)

    def get_latest_block_number(self) -> int:
        """Fetches the most recent block number from the connected node."""
        return self.w3.eth.block_number


class CrossChainRelayer:
    """
    Simulates relaying event data to a destination chain.
    In a real-world scenario, this component would sign a transaction for the destination chain.
    Here, it makes a POST request to a mock API endpoint.
    Includes robust retry logic for network reliability.
    """
    def __init__(self, api_endpoint: str, api_key: str):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.api_endpoint = api_endpoint
        self.headers = {
            'Content-Type': 'application/json',
            'X-API-Key': api_key
        }
        self.session = requests.Session()
        # Configure retry strategy for HTTP requests to handle transient network issues.
        retries = Retry(total=5, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        self.session.mount('https://', HTTPAdapter(max_retries=retries))

    def relay_event_data(self, event_data: Dict[str, Any]) -> bool:
        """
        Constructs a payload from the event data and sends it to the destination API.

        Args:
            event_data: A dictionary representing the decoded event.

        Returns:
            True if the data was relayed successfully, False otherwise.
        """
        payload = {
            'source_tx_hash': event_data['transactionHash'],
            'source_chain_id': event_data['chainId'],
            'event_type': 'TokensLocked',
            'payload': {
                'sender': event_data['args']['from'],
                'recipient': event_data['args']['to'],
                'amount': event_data['args']['amount'],
                'destination_chain_id': event_data['args']['destinationChainId']
            }
        }

        self.logger.info(f"Relaying event for Tx: {payload['source_tx_hash']}")
        try:
            response = self.session.post(self.api_endpoint, headers=self.headers, data=json.dumps(payload), timeout=10)
            response.raise_for_status()  # Raises HTTPError for bad responses (4xx or 5xx)
            self.logger.info(f"Successfully relayed event. API response: {response.json()}")
            return True
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Failed to relay event data to {self.api_endpoint}. Error: {e}")
            return False


class EventScanner:
    """
    Scans the source blockchain for specific smart contract events within a given block range.
    Manages its own state, tracking the last block it successfully scanned.
    """
    def __init__(self, connector: BlockchainConnector, contract: Contract, event_name: str, start_block: int, confirmations: int):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.connector = connector
        self.contract = contract
        self.event_name = event_name
        self.last_scanned_block = start_block - 1
        self.confirmations = confirmations
        self.event_filter = self.contract.events[event_name].create_filter(fromBlock='latest')

    def _get_scan_range(self) -> Optional[tuple[int, int]]:
        """
        Calculates the start and end blocks for the next scan.
        Ensures we only scan confirmed blocks to avoid issues with chain reorganizations.
        """
        try:
            latest_block = self.connector.get_latest_block_number()
        except Exception as e:
            self.logger.error(f"Could not fetch latest block number. Error: {e}")
            return None

        from_block = self.last_scanned_block + 1
        to_block = latest_block - self.confirmations

        if from_block > to_block:
            self.logger.info(f"No new confirmed blocks to scan. Current head: {latest_block}, last scanned: {self.last_scanned_block}")
            return None

        # To avoid overwhelming the RPC node, scan in smaller chunks if the range is too large.
        if to_block - from_block > 5000:
            to_block = from_block + 4999

        return from_block, to_block

    def scan(self) -> Generator[AttributeDict, None, None]:
        """
        Scans for new events and yields them one by one.
        Updates the last scanned block upon successful completion of a scan range.
        """
        scan_range = self._get_scan_range()
        if not scan_range:
            return

        from_block, to_block = scan_range
        self.logger.info(f"Scanning for '{self.event_name}' events from block {from_block} to {to_block}.")

        try:
            # Fetch logs for the specified event within the block range.
            logs = self.contract.events[self.event_name].get_logs(fromBlock=from_block, toBlock=to_block)
            
            if logs:
                self.logger.info(f"Found {len(logs)} new event(s) in range.")
                for event in logs:
                    yield event
            else:
                self.logger.info("No new events found in this range.")

            # If scan was successful, update the state.
            self.last_scanned_block = to_block

        except Exception as e:
            # Handle potential RPC errors, like timeouts or oversized responses.
            self.logger.error(f"An error occurred during event scanning: {e}")


class BridgeOrchestrator:
    """
    The main orchestrator class that wires all components together.
    It runs an infinite loop to periodically scan for and process new bridge events.
    """
    # A simplified ABI for a generic bridge contract's 'TokensLocked' event.
    BRIDGE_CONTRACT_ABI = json.loads('[{"anonymous":false,"inputs":[{"indexed":true,"internalType":"address","name":"from","type":"address"},{"indexed":true,"internalType":"address","name":"to","type":"address"},{"indexed":false,"internalType":"uint256","name":"amount","type":"uint256"},{"indexed":true,"internalType":"uint256","name":"destinationChainId","type":"uint256"}],"name":"TokensLocked","type":"event"}]')
    EVENT_NAME = 'TokensLocked'

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        try:
            self.config = Config()
            self.connector = BlockchainConnector(self.config.SOURCE_RPC_URL)
            self.contract = self.connector.get_contract(self.config.BRIDGE_CONTRACT_ADDRESS, self.BRIDGE_CONTRACT_ABI)
            self.scanner = EventScanner(
                self.connector, 
                self.contract, 
                self.EVENT_NAME, 
                self.config.START_BLOCK, 
                self.config.BLOCK_CONFIRMATIONS
            )
            self.relayer = CrossChainRelayer(self.config.DESTINATION_API_ENDPOINT, self.config.API_KEY)
            self.processed_txs = set()
        except (ValueError, ConnectionError) as e:
            self.logger.critical(f"Failed to initialize BridgeOrchestrator: {e}")
            exit(1)

    def _process_event(self, event: AttributeDict):
        """Handles the processing of a single event."""
        tx_hash = event['transactionHash'].hex()

        # Edge case: Prevent duplicate processing of the same transaction hash.
        if tx_hash in self.processed_txs:
            self.logger.warning(f"Event for Tx {tx_hash} has already been processed. Skipping.")
            return
        
        self.logger.info(f"Processing event from Tx: {tx_hash} in block {event['blockNumber']}")
        
        # Add a custom chainId field for the relayer, as it's not part of the standard event log.
        event_data_dict = {
            'args': event['args'],
            'transactionHash': tx_hash,
            'chainId': self.connector.w3.eth.chain_id
        }

        if self.relayer.relay_event_data(event_data_dict):
            self.processed_txs.add(tx_hash)
        else:
            self.logger.error(f"Failed to relay event for Tx {tx_hash}. It will be retried in the next cycle.")

    def run(self):
        """Starts the main event listening loop."""
        self.logger.info("Starting cross-chain bridge event listener...")
        while True:
            try:
                for event in self.scanner.scan():
                    self._process_event(event)
                
                self.logger.info(f"Scan cycle complete. Waiting for {self.config.POLL_INTERVAL_SECONDS} seconds...")
                time.sleep(self.config.POLL_INTERVAL_SECONDS)

            except KeyboardInterrupt:
                self.logger.info("Shutdown signal received. Exiting...")
                break
            except Exception as e:
                self.logger.critical(f"An unexpected critical error occurred in the main loop: {e}", exc_info=True)
                self.logger.info("Attempting to recover in 30 seconds...")
                time.sleep(30)


if __name__ == "__main__":
    orchestrator = BridgeOrchestrator()
    orchestrator.run()

# @-internal-utility-start
def get_config_value_1776(key: str):
    """Reads a value from a simple key-value config. Added on 2025-12-01 22:18:23"""
    with open('config.ini', 'r') as f:
        for line in f:
            if line.startswith(key):
                return line.split('=')[1].strip()
    return None
# @-internal-utility-end

