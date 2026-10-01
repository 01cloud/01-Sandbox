# Copyright 2026 Alibaba Group Holding Ltd. & 01Sandbox Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
OCM (Open Cluster Management) multi-cluster workload provider implementation.
Dispatches Sandbox Pod workloads to Spoke worker clusters via OCM ManifestWork Custom Resources.
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from kubernetes.client import ApiException
from src.api.schema import Endpoint, ImageSpec, NetworkPolicy, Volume
from src.config import INGRESS_MODE_GATEWAY, AppConfig
from src.services.helpers import format_ingress_endpoint
from src.services.k8s.client import K8sClient
from src.services.k8s.workload_provider import WorkloadProvider
from src.services.runtime_resolver import SecureRuntimeResolver

logger = logging.getLogger(__name__)

# OCM API CRD Constants
OCM_GROUP = "work.open-cluster-management.io"
OCM_VERSION = "v1"
OCM_PLURAL = "manifestworks"

DEFAULT_PLACEMENT_NAME = "sandbox-spoke-placement"
DEFAULT_FALLBACK_SPOKE = "spoke1"


class OcmWorkloadProvider(WorkloadProvider):
    """
    OCM-based Multi-Cluster Workload Provider.

    Instead of provisioning Sandbox Pods directly on the local Hub Kubernetes API,
    OcmWorkloadProvider wraps Pod specifications into OCM ManifestWork Custom Resources,
    allowing OCM klusterlet to sync and execute the sandbox workload on target Spoke clusters.
    """

    def __init__(
        self,
        k8s_client: K8sClient,
        app_config: Optional[AppConfig] = None,
        placement_name: str = DEFAULT_PLACEMENT_NAME,
    ):
        """
        Initialize OCM Workload Provider.

        Args:
            k8s_client: K8s client instance wrapper
            app_config: Application configuration object
            placement_name: Name of the OCM Placement resource for spoke selection
        """
        self.k8s_client = k8s_client
        if (
            not hasattr(self.k8s_client, "custom_api")
            or self.k8s_client.custom_api is None
        ):
            self.k8s_client.custom_api = self.k8s_client.get_custom_objects_api()
        self.app_config = app_config
        self.ingress_config = app_config.ingress if app_config else None

        k8s_config = app_config.kubernetes if app_config else None
        if k8s_config and getattr(k8s_config, "placement_name", None):
            self.placement_name = k8s_config.placement_name
        else:
            self.placement_name = placement_name

        self.resolver = SecureRuntimeResolver(app_config) if app_config else None
        self.runtime_class = (
            self.resolver.get_k8s_runtime_class() if self.resolver else "gvisor"
        )

    def supports_image_auth(self) -> bool:
        """OCM provider supports image pull authentication via pod imagePullSecrets."""
        return True

    def create_workload(
        self,
        sandbox_id: str,
        namespace: str,
        image_spec: ImageSpec,
        entrypoint: List[str],
        env: Dict[str, str],
        resource_limits: Dict[str, str],
        labels: Dict[str, str],
        expires_at: Optional[datetime],
        execd_image: str,
        extensions: Optional[Dict[str, Any]] = None,
        network_policy: Optional[NetworkPolicy] = None,
        egress_image: Optional[str] = None,
        volumes: Optional[List[Volume]] = None,
    ) -> Dict[str, Any]:
        """
        Create a new sandbox pod workload wrapped inside an OCM ManifestWork resource.

        1. Resolves target spoke cluster namespace on Hub from extensions or placement.
        2. Constructs the execution Pod manifest.
        3. Wraps Pod and supporting resources in OCM ManifestWork CR spec.
        4. Submits ManifestWork to the Hub K8s API under the spoke cluster namespace.
        """
        target_cluster = self._resolve_target_spoke_cluster(extensions)
        workload_namespace = "opensandbox-workloads"

        manifests: List[Dict[str, Any]] = []

        # Check if code files are embedded in extensions for remote spoke delivery
        cm_name = None
        raw_files = (
            extensions.get("files")
            if extensions and isinstance(extensions, dict)
            else None
        )
        files_dict = None
        if raw_files:
            if isinstance(raw_files, str):
                try:
                    files_dict = json.loads(raw_files)
                except Exception:
                    files_dict = None
            elif isinstance(raw_files, dict):
                files_dict = raw_files

        if files_dict and isinstance(files_dict, dict):
            cm_name = f"cm-code-{sandbox_id[:12]}"
            data_map = {}
            for fname, content in files_dict.items():
                safe_name = os.path.basename(fname)
                data_map[safe_name] = content
            manifests.append(
                {
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": {
                        "name": cm_name,
                        "namespace": workload_namespace,
                    },
                    "data": data_map,
                }
            )

        pod_manifest = self._build_sandbox_pod_manifest(
            sandbox_id=sandbox_id,
            namespace=workload_namespace,
            image_spec=image_spec,
            entrypoint=entrypoint,
            env=env,
            resource_limits=resource_limits,
            labels=labels,
            execd_image=execd_image,
            extensions=extensions,
            volumes=volumes,
            cm_name=cm_name,
        )
        manifests.append(pod_manifest)

        manifest_work_name = f"mw-sandbox-{sandbox_id}"
        work_labels = {
            "sandbox.opensandbox.io/id": sandbox_id,
            "app.kubernetes.io/part-of": "01sandbox",
            "opensandbox.io/spoke-cluster": target_cluster,
        }
        if labels:
            work_labels.update(labels)

        annotations = {}
        if expires_at:
            annotations["sandbox.opensandbox.io/expires-at"] = expires_at.isoformat()

        manifest_work = {
            "apiVersion": f"{OCM_GROUP}/{OCM_VERSION}",
            "kind": "ManifestWork",
            "metadata": {
                "name": manifest_work_name,
                "namespace": target_cluster,  # Spoke cluster namespace on Hub
                "labels": work_labels,
                "annotations": annotations,
            },
            "spec": {
                "workload": {"manifests": manifests},
                "manifestConfigs": [
                    {
                        "resourceIdentifier": {
                            "group": "",
                            "resource": "pods",
                            "name": pod_manifest["metadata"]["name"],
                            "namespace": workload_namespace,
                        },
                        "feedbackRules": [
                            {
                                "type": "JSONPaths",
                                "jsonPaths": [
                                    {"name": "phase", "path": ".status.phase"}
                                ],
                            }
                        ],
                    }
                ],
            },
        }

        logger.info(
            "Submitting OCM ManifestWork %s targeting spoke cluster: %s (namespace: %s)",
            manifest_work_name,
            target_cluster,
            namespace,
        )

        try:
            self.k8s_client.custom_api.create_namespaced_custom_object(
                group=OCM_GROUP,
                version=OCM_VERSION,
                namespace=target_cluster,
                plural=OCM_PLURAL,
                body=manifest_work,
            )
        except ApiException as e:
            logger.error(
                "Failed to create OCM ManifestWork %s: %s", manifest_work_name, e
            )
            raise e

        return {
            "name": manifest_work_name,
            "sandbox_id": sandbox_id,
            "spoke_cluster": target_cluster,
            "status": "Pending",
        }

    def _resolve_target_spoke_cluster(
        self, extensions: Optional[Dict[str, Any]]
    ) -> str:
        """Resolve target spoke cluster from request extensions, placement engine, or fallback."""
        if extensions and isinstance(extensions, dict):
            if extensions.get("target_cluster"):
                return str(extensions["target_cluster"])
            if extensions.get("spoke_cluster"):
                return str(extensions["spoke_cluster"])
            region = str(extensions.get("region", "")).lower().strip()
            if region in ("us-east-1", "us"):
                return "spoke1"
            elif region in ("eu-central-1", "eu"):
                return "spoke2"

        # Dynamic query of OCM PlacementDecision
        try:
            decisions = self.k8s_client.custom_api.list_namespaced_custom_object(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                namespace="opensandbox-system",
                plural="placementdecisions",
                label_selector=f"cluster.open-cluster-management.io/placement={self.placement_name}",
            )
            items = decisions.get("items", [])
            if items:
                status_decisions = items[0].get("status", {}).get("decisions", [])
                if status_decisions and status_decisions[0].get("clusterName"):
                    selected = status_decisions[0]["clusterName"]
                    logger.info(
                        "Resolved spoke cluster '%s' from OCM PlacementDecision",
                        selected,
                    )
                    return selected
        except Exception as e:
            logger.warning(
                "Could not query PlacementDecision for spoke selection: %s", e
            )

        # Default fallback spoke cluster
        return DEFAULT_FALLBACK_SPOKE

    def _build_sandbox_pod_manifest(
        self,
        sandbox_id: str,
        namespace: str,
        image_spec: ImageSpec,
        entrypoint: List[str],
        env: Dict[str, str],
        resource_limits: Dict[str, str],
        labels: Dict[str, str],
        execd_image: str,
        extensions: Optional[Dict[str, Any]] = None,
        volumes: Optional[List[Volume]] = None,
        cm_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build raw K8s Pod dictionary specification to embed in ManifestWork."""
        # Safely extract image repository/URI from ImageSpec
        image_name = getattr(image_spec, "uri", None)
        if not image_name:
            repo = getattr(image_spec, "repository", None)
            tag = getattr(image_spec, "tag", None)
            if repo and tag:
                image_name = f"{repo}:{tag}"
            elif repo:
                image_name = repo
            else:
                image_name = str(image_spec)

        env_dict = dict(env or {})
        if "EXCLUDE_TOOLS" not in env_dict:
            env_dict["EXCLUDE_TOOLS"] = "trivy,gitleaks"
        if cm_name:
            env_dict["SCAN_DIR"] = "/workspace"
            env_dict["SCAN_REPORT"] = "/reports/security_scan_report.json"

        container_env = [{"name": k, "value": str(v)} for k, v in env_dict.items()]

        pod_labels = {
            "sandbox.opensandbox.io/id": sandbox_id,
            "app.kubernetes.io/part-of": "01sandbox",
            "app": "opensandbox-pod",
        }
        if labels:
            pod_labels.update(labels)

        # Secure runtime selection (gvisor / kata)
        secure_runtime = "gvisor"
        if extensions and "runtimeClassName" in extensions:
            secure_runtime = str(extensions["runtimeClassName"])
        elif extensions and "secure_runtime" in extensions:
            secure_runtime = str(extensions["secure_runtime"])
        elif self.runtime_class:
            secure_runtime = self.runtime_class

        # Volume mounts
        pod_volumes = [{"name": "opensandbox-bin", "emptyDir": {"sizeLimit": "1Gi"}}]
        container_volume_mounts = [
            {"name": "opensandbox-bin", "mountPath": "/opt/opensandbox/bin"}
        ]

        if cm_name:
            pod_volumes.append({"name": "code-volume", "configMap": {"name": cm_name}})
            container_volume_mounts.append(
                {"name": "code-volume", "mountPath": "/workspace"}
            )
            pod_volumes.append({"name": "reports-volume", "emptyDir": {}})
            container_volume_mounts.append(
                {"name": "reports-volume", "mountPath": "/reports"}
            )
            pod_volumes.append({"name": "data-volume", "emptyDir": {}})
            container_volume_mounts.append(
                {"name": "data-volume", "mountPath": "/data"}
            )
        elif volumes:
            for idx, vol in enumerate(volumes):
                v_name = f"vol-{idx}"
                if getattr(vol, "host_path", None):
                    pod_volumes.append(
                        {"name": v_name, "hostPath": {"path": vol.host_path}}
                    )
                elif getattr(vol, "pvc_name", None):
                    pod_volumes.append(
                        {
                            "name": v_name,
                            "persistentVolumeClaim": {"claimName": vol.pvc_name},
                        }
                    )
                container_volume_mounts.append(
                    {"name": v_name, "mountPath": vol.mount_path}
                )

        pod_name = (
            f"sbx-{sandbox_id[:12]}" if len(sandbox_id) > 12 else f"sbx-{sandbox_id}"
        )

        actual_job_id = sandbox_id
        if extensions and isinstance(extensions, dict):
            actual_job_id = extensions.get("job_id") or sandbox_id

        callback_url = (
            os.environ.get("REPORT_CALLBACK_URL") or "http://10.99.0.100/api/v1/01sbx"
        ).rstrip("/")

        upload_script = (
            f"rm -f /usr/local/bin/trivy /usr/local/bin/gitleaks; "
            f"/opt/opensandbox/code-interpreter.sh; "
            f"if [ -f /reports/security_scan_report.json ]; then "
            f"curl -s -X POST -H 'Content-Type: application/json' -d @/reports/security_scan_report.json {callback_url}/scan-jobs/{actual_job_id}/report || true; "
            f"fi"
        )

        pod_spec = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": pod_name,
                "namespace": namespace or "opensandbox-workloads",
                "labels": pod_labels,
            },
            "spec": {
                "runtimeClassName": secure_runtime,
                "restartPolicy": "Never",
                "containers": [
                    {
                        "name": "code-interpreter",
                        "image": image_name,
                        "command": ["/bin/bash", "-c", upload_script],
                        "env": container_env,
                        "ports": [
                            {"containerPort": 44772},
                            {"containerPort": 54321},
                        ],
                        "resources": {
                            "limits": {
                                "cpu": "1000m",
                                "memory": "1536Mi",
                            },
                            "requests": {
                                "cpu": "100m",
                                "memory": "128Mi",
                            },
                        },
                        "volumeMounts": container_volume_mounts,
                    }
                ],
                "volumes": pod_volumes,
            },
        }

        # Filter None entries
        if not entrypoint:
            pod_spec["spec"]["containers"][0].pop("command", None)

        return pod_spec

    def get_workload(self, sandbox_id: str, namespace: str) -> Optional[Any]:
        """Fetch OCM ManifestWork resource by sandbox_id across spoke cluster namespaces."""
        manifest_work_name = f"mw-sandbox-{sandbox_id}"

        # Search provided namespace / target cluster namespace first
        for target_ns in [namespace, DEFAULT_FALLBACK_SPOKE]:
            if not target_ns:
                continue
            try:
                res = self.k8s_client.custom_api.get_namespaced_custom_object(
                    group=OCM_GROUP,
                    version=OCM_VERSION,
                    namespace=target_ns,
                    plural=OCM_PLURAL,
                    name=manifest_work_name,
                )
                return res
            except ApiException as e:
                if e.status != 404:
                    logger.error(
                        "Error retrieving ManifestWork %s in %s: %s",
                        manifest_work_name,
                        target_ns,
                        e,
                    )

        # Fallback list query
        label_selector = f"sandbox.opensandbox.io/id={sandbox_id}"
        works = self.list_workloads(namespace="", label_selector=label_selector)
        return works[0] if works else None

    def delete_workload(self, sandbox_id: str, namespace: str) -> None:
        """Delete OCM ManifestWork resource from Hub cluster."""
        workload = self.get_workload(sandbox_id, namespace)
        if not workload:
            logger.warning(
                "ManifestWork for sandbox %s not found for deletion", sandbox_id
            )
            return

        mw_name = workload["metadata"]["name"]
        target_ns = workload["metadata"]["namespace"]

        try:
            self.k8s_client.custom_api.delete_namespaced_custom_object(
                group=OCM_GROUP,
                version=OCM_VERSION,
                namespace=target_ns,
                plural=OCM_PLURAL,
                name=mw_name,
            )
            logger.info(
                "Successfully deleted ManifestWork %s in spoke namespace %s",
                mw_name,
                target_ns,
            )
        except ApiException as e:
            if e.status != 404:
                logger.error("Failed to delete ManifestWork %s: %s", mw_name, e)
                raise e

    def list_workloads(self, namespace: str, label_selector: str) -> List[Any]:
        """List OCM ManifestWork resources matching label_selector."""
        try:
            if namespace:
                res = self.k8s_client.custom_api.list_namespaced_custom_object(
                    group=OCM_GROUP,
                    version=OCM_VERSION,
                    namespace=namespace,
                    plural=OCM_PLURAL,
                    label_selector=label_selector,
                )
            else:
                res = self.k8s_client.custom_api.list_cluster_custom_object(
                    group=OCM_GROUP,
                    version=OCM_VERSION,
                    plural=OCM_PLURAL,
                    label_selector=label_selector,
                )
            return res.get("items", [])
        except ApiException as e:
            logger.error("Failed to list ManifestWorks: %s", e)
            return []

    def update_expiration(
        self, sandbox_id: str, namespace: str, expires_at: datetime
    ) -> None:
        """Update sandbox expiration annotation on ManifestWork."""
        workload = self.get_workload(sandbox_id, namespace)
        if not workload:
            raise ValueError(f"Workload not found for sandbox {sandbox_id}")

        mw_name = workload["metadata"]["name"]
        target_ns = workload["metadata"]["namespace"]

        patch = {
            "metadata": {
                "annotations": {
                    "sandbox.opensandbox.io/expires-at": expires_at.isoformat()
                }
            }
        }
        self.k8s_client.custom_api.patch_namespaced_custom_object(
            group=OCM_GROUP,
            version=OCM_VERSION,
            namespace=target_ns,
            plural=OCM_PLURAL,
            name=mw_name,
            body=patch,
        )

    def get_expiration(self, workload: Any) -> Optional[datetime]:
        """Extract expiration datetime from ManifestWork annotations."""
        if not isinstance(workload, dict):
            return None
        annotations = workload.get("metadata", {}).get("annotations", {})
        exp_str = annotations.get("sandbox.opensandbox.io/expires-at")
        if exp_str:
            try:
                return datetime.fromisoformat(exp_str)
            except ValueError:
                pass
        return None

    def get_status(self, workload: Any) -> Dict[str, Any]:
        """Extract status dictionary from OCM ManifestWork resource."""
        if not isinstance(workload, dict):
            return {
                "state": "Unknown",
                "reason": "InvalidWorkloadObject",
                "message": "",
                "last_transition_at": None,
            }

        status_block = workload.get("status", {})
        conditions = status_block.get("conditions", [])

        applied = False
        available = False
        message = ""
        reason = ""
        last_transition = None

        for cond in conditions:
            c_type = cond.get("type")
            c_status = cond.get("status")
            if c_type == "Applied" and c_status == "True":
                applied = True
            if c_type == "Available" and c_status == "True":
                available = True
                message = cond.get("message", "")
                reason = cond.get("reason", "")
                last_transition = cond.get("lastTransitionTime")

        if applied and available:
            state = "Running"
        elif applied:
            state = "Pending"
        else:
            state = "Creating"

        # Check feedbackRules status from Klusterlet for Pod phase
        resource_status = status_block.get("resourceStatus", {}).get("manifests", [])
        for m in resource_status:
            feedbacks = m.get("statusFeedbacks", {}).get("values", [])
            for val in feedbacks:
                if val.get("name") == "phase":
                    phase = val.get("fieldValue", {}).get("string", "")
                    if phase in ("Succeeded", "Completed"):
                        state = "Completed"
                        reason = "PodSucceeded"
                    elif phase == "Failed":
                        state = "Failed"
                        reason = "PodFailed"
                    elif phase == "Running":
                        state = "Running"

        return {
            "state": state,
            "reason": reason or "OCMManifestWork",
            "message": message
            or f"Spoke cluster: {workload.get('metadata', {}).get('namespace')}",
            "last_transition_at": last_transition,
        }

    def get_endpoint_info(
        self, workload: Any, port: int, sandbox_id: str
    ) -> Optional[Endpoint]:
        """Retrieve endpoint routing information for sandbox workload."""
        if self.ingress_config and self.ingress_config.mode == INGRESS_MODE_GATEWAY:
            return format_ingress_endpoint(self.ingress_config, sandbox_id, port)

        spoke_ns = (
            workload.get("metadata", {}).get("namespace", DEFAULT_FALLBACK_SPOKE)
            if isinstance(workload, dict)
            else DEFAULT_FALLBACK_SPOKE
        )
        return Endpoint(
            host=f"sandbox-{sandbox_id}.{spoke_ns}.svc.cluster.local",
            port=port,
        )
