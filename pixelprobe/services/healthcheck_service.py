"""
Healthchecks.io integration service for PixelProbe

Supports both public healthchecks.io and self-hosted instances.
"""

import logging
import requests
from typing import Optional, Dict
from datetime import datetime
from pixelprobe.utils.security import validate_safe_url, create_safe_session

logger = logging.getLogger(__name__)


class HealthcheckService:
    """Service for managing healthchecks.io integrations"""

    def __init__(self):
        self.session = create_safe_session()
        self.session.headers.update({
            'User-Agent': 'PixelProbe-Healthcheck/1.0'
        })

    def ping_start(self, healthcheck_url: str) -> bool:
        """Ping healthcheck at scan start

        Args:
            healthcheck_url: The healthcheck ping URL

        Returns:
            bool: True if ping succeeded, False otherwise
        """
        try:
            if not healthcheck_url:
                logger.warning("No healthcheck URL provided")
                return False

            # SSRF protection: validate URL before making request
            is_safe, error = validate_safe_url(healthcheck_url)
            if not is_safe:
                logger.warning(f"Healthcheck start ping blocked (SSRF): {error}")
                return False

            # Healthchecks.io uses /start slug for start signals
            start_url = f"{healthcheck_url.rstrip('/')}/start"
            logger.info(f"Sending healthcheck start ping to: {start_url}")

            response = self.session.get(start_url, timeout=10)
            response.raise_for_status()

            logger.info(f"Healthcheck start ping successful (status: {response.status_code})")
            return True

        except requests.exceptions.Timeout:
            logger.error(f"Healthcheck start ping timed out for URL: {healthcheck_url}")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"Healthcheck start ping failed for URL {healthcheck_url}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error in healthcheck start ping for URL {healthcheck_url}: {e}")
            return False

    def ping_success(self, healthcheck_url: str, report_data: Optional[Dict] = None) -> bool:
        """Ping healthcheck with success status and optional report data

        Args:
            healthcheck_url: The healthcheck ping URL
            report_data: Optional scan report data to include

        Returns:
            bool: True if ping succeeded, False otherwise
        """
        try:
            if not healthcheck_url:
                logger.warning("No healthcheck URL provided")
                return False

            # SSRF protection: validate URL before making request
            is_safe, error = validate_safe_url(healthcheck_url)
            if not is_safe:
                logger.warning(f"Healthcheck success ping blocked (SSRF): {error}")
                return False

            logger.info(f"Sending healthcheck success ping to: {healthcheck_url}")

            # Format report data if provided
            if report_data:
                # Build a text summary of the scan report
                message_lines = [
                    f"Report ID: {report_data.get('report_id', 'N/A')}",
                    f"Scan Type: {report_data.get('scan_type', 'N/A').upper().replace('_', ' ')}",
                    f"Status: {report_data.get('status', 'N/A')}",
                    f"Start Time: {report_data.get('start_time', 'N/A')}",
                    f"End Time: {report_data.get('end_time', 'N/A')}",
                    f"Duration: {self._format_duration(report_data.get('duration_seconds'))}",
                    "",
                    "Summary Statistics:",
                    f"  Corrupted Files: {report_data.get('files_corrupted', 0)}",
                    f"  Error Files: {report_data.get('files_error', 0)}",
                    f"  Files With Warnings: {report_data.get('files_with_warnings', 0)}",
                    f"  New Files: {report_data.get('files_added', 0)}",
                    f"  Success Rate: {self._calculate_success_rate(report_data)}",
                    f"  Total Files: {report_data.get('files_scanned', 0)}",
                    f"  Updated Files: {report_data.get('files_updated', 0)}"
                ]
                message = "\n".join(message_lines)

                # POST with message body
                response = self.session.post(
                    healthcheck_url,
                    data=message.encode('utf-8'),
                    headers={'Content-Type': 'text/plain'},
                    timeout=10
                )
            else:
                # Simple GET ping
                response = self.session.get(healthcheck_url, timeout=10)

            response.raise_for_status()

            logger.info(f"Healthcheck success ping successful (status: {response.status_code})")
            return True

        except requests.exceptions.Timeout:
            logger.error(f"Healthcheck success ping timed out for URL: {healthcheck_url}")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"Healthcheck success ping failed for URL {healthcheck_url}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error in healthcheck success ping for URL {healthcheck_url}: {e}")
            return False

    def ping_fail(self, healthcheck_url: str, error_message: Optional[str] = None) -> bool:
        """Ping healthcheck with failure status

        Args:
            healthcheck_url: The healthcheck ping URL
            error_message: Optional error message to include

        Returns:
            bool: True if ping succeeded, False otherwise
        """
        try:
            if not healthcheck_url:
                logger.warning("No healthcheck URL provided")
                return False

            # SSRF protection: validate URL before making request
            is_safe, error = validate_safe_url(healthcheck_url)
            if not is_safe:
                logger.warning(f"Healthcheck failure ping blocked (SSRF): {error}")
                return False

            # Healthchecks.io uses /fail slug for failure signals
            fail_url = f"{healthcheck_url.rstrip('/')}/fail"
            logger.info(f"Sending healthcheck failure ping to: {fail_url}")

            if error_message:
                # POST with error message
                response = self.session.post(
                    fail_url,
                    data=error_message.encode('utf-8'),
                    headers={'Content-Type': 'text/plain'},
                    timeout=10
                )
            else:
                # Simple GET ping
                response = self.session.get(fail_url, timeout=10)

            response.raise_for_status()

            logger.info(f"Healthcheck failure ping successful (status: {response.status_code})")
            return True

        except requests.exceptions.Timeout:
            logger.error(f"Healthcheck failure ping timed out for URL: {healthcheck_url}")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"Healthcheck failure ping failed for URL {healthcheck_url}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error in healthcheck failure ping: {e}")
            return False

    def _format_duration(self, duration_seconds: Optional[float]) -> str:
        """Format duration in human-readable format

        Args:
            duration_seconds: Duration in seconds

        Returns:
            str: Formatted duration string
        """
        if duration_seconds is None:
            return "N/A"

        try:
            duration = int(duration_seconds)
            minutes = duration // 60
            seconds = duration % 60

            if minutes > 0:
                return f"{minutes}m {seconds}s"
            else:
                return f"{seconds}s"
        except (ValueError, TypeError):
            return "N/A"

    def _calculate_success_rate(self, report_data: Dict) -> str:
        """Calculate success rate from report data

        Args:
            report_data: Scan report data

        Returns:
            str: Success rate percentage
        """
        try:
            total_files = report_data.get('files_scanned', 0)
            corrupted = report_data.get('files_corrupted', 0)
            errors = report_data.get('files_error', 0)

            if total_files == 0:
                return "N/A"

            failed = corrupted + errors
            success_rate = ((total_files - failed) / total_files) * 100

            return f"{success_rate:.1f}%"
        except (ValueError, TypeError, ZeroDivisionError):
            return "N/A"

    def validate_url(self, healthcheck_url: str) -> tuple[bool, Optional[str]]:
        """Validate a healthcheck URL format

        Args:
            healthcheck_url: The URL to validate

        Returns:
            tuple: (is_valid, error_message)
        """
        if not healthcheck_url:
            return False, "Healthcheck URL is required"

        # Basic URL validation
        if not healthcheck_url.startswith(('http://', 'https://')):
            return False, "Healthcheck URL must start with http:// or https://"

        # SSRF protection: validate against private IP ranges
        is_safe, ssrf_error = validate_safe_url(healthcheck_url)
        if not is_safe:
            return False, f"URL blocked by security policy: {ssrf_error}"

        # Log the URL format for debugging
        # Supports both public hc-ping.com and self-hosted instances
        # Public: https://hc-ping.com/UUID
        # Self-hosted: https://your-domain.com/ping/UUID (or custom paths)
        logger.debug(f"Validating healthcheck URL: {healthcheck_url}")

        return True, None
