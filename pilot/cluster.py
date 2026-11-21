import os

class Drift(Exception):
    """The target changed outside this release."""

class Cluster:
    def __init__(self):
        from kubernetes import client, config
        if os.getenv("KUBERNETES_SERVICE_HOST"):
            config.load_incluster_config()
        else:
            config.load_kube_config(context=os.getenv("KUBE_CONTEXT", "kind-releasepilot"))
        self.api = client.AppsV1Api()

    def current(self, service):
        obj = self.api.read_namespaced_deployment(service.deployment, service.namespace, _request_timeout=8)
        if (obj.metadata.labels or {}).get("app.kubernetes.io/managed-by") != "releasepilot":
            raise Drift("Deployment is missing ReleasePilot ownership")
        containers = obj.spec.template.spec.containers
        container = next((c for c in containers if c.name == service.container), None)
        if not container:
            raise Drift("Configured container is missing")
        marker = (obj.spec.template.metadata.labels or {}).get("releasepilot-id")
        return obj, container.image, marker

    def apply(self, service, image, marker, expected_image, expected_marker):
        obj, current, observed = self.current(service)
        if (current, observed) == (image, str(marker)):
            return
        if observed != (str(expected_marker) if expected_marker is not None else None):
            raise Drift("Live release label differs from recorded state")
        if expected_image is not None and current != expected_image:
            raise Drift("Live image differs from recorded state")
        patch = {"metadata":{"resourceVersion":obj.metadata.resource_version},
                 "spec":{"template":{"metadata":{"labels":{"releasepilot-id":str(marker)}},
                         "spec":{"containers":[{"name":service.container, "image":image}]}}}}
        self.api.patch_namespaced_deployment(service.deployment, service.namespace, patch, _request_timeout=8)

    def ready(self, service, image, marker):
        obj, current, observed = self.current(service)
        if (current, observed) != (image, str(marker)):
            raise Drift("Live deployment differs from the requested release")
        status = obj.status
        if (status.observed_generation or 0) < obj.metadata.generation:
            return False
        if any(c.type=="Progressing" and c.status=="False" and c.reason=="ProgressDeadlineExceeded"
               for c in status.conditions or []):
            raise RuntimeError("Deployment exceeded its progress deadline")
        desired = obj.spec.replicas
        return bool(desired and all(count == desired for count in
                    (status.updated_replicas, status.ready_replicas, status.available_replicas, status.replicas)))
