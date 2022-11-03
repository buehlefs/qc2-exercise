from datetime import datetime, timezone
from time import sleep, time

from qiskit.providers import JobError, JobTimeoutError
from qiskit.providers import JobV1 as Job
from qiskit.providers.jobstatus import JobStatus
from qiskit.result import Counts, Result
from requests import request


class QHAnaJob(Job):
    """A job class for retreiving circuit execution results from a QHAna plugin."""

    def __init__(self, backend, job_id, circuits):
        """Create a new job.

        Args:
            backend (BackendV2): the backend that created this job
            job_id (str): the id of the job (use the full result URL as the job id)
            circuits (QuantumCircuit): the quantum circuit executed in this job
        """
        super().__init__(backend, job_id)
        self._backend = backend
        self.circuits = circuits
        self._status = None
        self._result = None

    def _wait_for_result(self, timeout=None, wait=5):
        """Wait for the circuit simulation results.

        Args:
            timeout (float, optional): a timeout in seconds, after which an error will be raised. Defaults to None.
            wait (int, optional): the wait time in seconds between attempts to query the job status. Defaults to 5.

        Raises:
            JobTimeoutError: raised if the timout was exceeded
            JobError: raised if the job was unsuccessful

        Returns:
            dict: the QHAna plugin result
        """
        start_time = time()
        result = None
        while True:
            elapsed = time() - start_time
            if timeout and elapsed >= timeout:
                raise JobTimeoutError("Timed out waiting for result")
            result = self._fetch_qhana_result()
            if result["status"] == "SUCCESS":
                break
            if result["status"] == "FAILURE":
                raise JobError("Job error")
            sleep(wait)
        return result

    def result(self, timeout=None, wait=5):
        """Get the job result (blocks if result is not yet available).

        Args:
            timeout (float, optional): a timeout in seconds, after which an error will be raised. Defaults to None.
            wait (int, optional): the wait time in seconds between attempts to query the job status. Defaults to 5.

        Returns:
            Result: the job result
        """
        if self._status is None or self._result is None:
            result = self._wait_for_result(timeout, wait)
            counts = self._fetch_result_counts(result)
            results = [{"success": True, "shots": sum(counts.values()), "data": counts}]
            self._status = JobStatus.DONE
            self._result = Result.from_dict(
                {
                    "results": results,
                    "backend_name": self._backend.name,
                    "backend_version": self._backend.backend_version,
                    "job_id": self._job_id,
                    "qobj_id": ", ".join([self.circuits.name]),
                    "success": True,
                    "status": self._status,
                    "date": datetime.now(timezone.utc),
                }
            )
        return self._result

    def status(self):
        """Get the current job status.

        Returns:
            JobStatus: the current job status
        """
        if self._status is None:
            result = self._fetch_qhana_result()
            if result["status"] == "PENDING":
                return JobStatus.RUNNING
            elif result["status"] == "SUCCESS":
                self._status = JobStatus.DONE
            else:
                self._status = JobStatus.ERROR
        return self._status if self._status else JobStatus.RUNNING

    def submit(self):
        raise NotImplementedError

    def _fetch_qhana_result(self):
        """Fetch the curent QHAna result including the result status."""
        return request("get", self._job_id, timeout=1).json()

    def _fetch_result_counts(self, result: dict) -> Counts:
        """Fetch the measurment counts.

        Args:
            result (dict): the QHAna result dict as returned by ``_fetch_qhana_result``

        Returns:
            Counts: the measurment counts
        """
        # filter outputs urls by output name
        counts_urls = [
            o["href"] for o in result["outputs"] if "result-counts" in o["name"]
        ]
        counts_url = counts_urls[0]  # use first url

        # fetch as json
        counts = request("get", counts_url).json()

        # remove ID and href attr of entities
        counts.pop("ID", None)
        counts.pop("href", None)

        # convert to Counts dict
        return Counts(counts)
