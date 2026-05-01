from mcp.server.fastmcp import FastMCP
from netbox_client import NetBoxRestClient
import os
import re
from urllib.parse import urljoin
import requests
import socket
from urllib.parse import urlparse, urlunparse

# Mapping of simple object names to API endpoints
NETBOX_OBJECT_TYPES_BASE = {
    # DCIM (Device and Infrastructure)
    "cables": "dcim/cables",
    "console-ports": "dcim/console-ports", 
    "console-server-ports": "dcim/console-server-ports",
    "console-port-templates": "dcim/console-port-templates",
    "console-server-port-templates": "dcim/console-server-port-templates",
    "devices": "dcim/devices",
    "device-bays": "dcim/device-bays",
    "device-bay-templates": "dcim/device-bay-templates",
    "device-roles": "dcim/device-roles",
    "device-types": "dcim/device-types",
    # Device type component templates
    "interface-templates": "dcim/interface-templates",
    "power-port-templates": "dcim/power-port-templates",
    "power-outlet-templates": "dcim/power-outlet-templates",
    "console-port-templates": "dcim/console-port-templates",
    "console-server-port-templates": "dcim/console-server-port-templates",
    "front-port-templates": "dcim/front-port-templates",
    "rear-port-templates": "dcim/rear-port-templates",
    "device-bay-templates": "dcim/device-bay-templates",
    "front-ports": "dcim/front-ports",
    "interfaces": "dcim/interfaces",
    "interface-templates": "dcim/interface-templates",
    "inventory-items": "dcim/inventory-items",
    "inventory-item-templates": "dcim/inventory-item-templates",
    "locations": "dcim/locations",
    "manufacturers": "dcim/manufacturers",
    "platforms": "dcim/platforms",
    "power-feeds": "dcim/power-feeds",
    "power-outlets": "dcim/power-outlets",
    "power-outlet-templates": "dcim/power-outlet-templates",
    "power-panels": "dcim/power-panels",
    "power-ports": "dcim/power-ports",
    "power-port-templates": "dcim/power-port-templates",
    "racks": "dcim/racks",
    "rack-reservations": "dcim/rack-reservations",
    "rack-roles": "dcim/rack-roles",
    "rack-types": "dcim/rack-types",
    "regions": "dcim/regions",
    "sites": "dcim/sites",
    "site-groups": "dcim/site-groups",
    "virtual-chassis": "dcim/virtual-chassis",

    # NetBox 4.x introduces dcim/mac-addresses. We include it here and
    # capability-detect at runtime to keep safe-by-default behavior.
    "mac-addresses": "dcim/mac-addresses",
    
    # IPAM (IP Address Management)
    "asns": "ipam/asns",
    "asn-ranges": "ipam/asn-ranges", 
    "aggregates": "ipam/aggregates",
    "fhrp-groups": "ipam/fhrp-groups",
    "ip-addresses": "ipam/ip-addresses",
    "ip-ranges": "ipam/ip-ranges",
    "prefixes": "ipam/prefixes",
    "rirs": "ipam/rirs",
    "roles": "ipam/roles",
    "route-targets": "ipam/route-targets",
    "services": "ipam/services",
    "vlans": "ipam/vlans",
    "vlan-groups": "ipam/vlan-groups",
    "vrfs": "ipam/vrfs",
    
    # Circuits
    "circuits": "circuits/circuits",
    "circuit-types": "circuits/circuit-types",
    "circuit-terminations": "circuits/circuit-terminations",
    "providers": "circuits/providers",
    "provider-networks": "circuits/provider-networks",
    
    # Virtualization
    "clusters": "virtualization/clusters",
    "cluster-groups": "virtualization/cluster-groups",
    "cluster-types": "virtualization/cluster-types",
    "virtual-machines": "virtualization/virtual-machines",
    "vm-interfaces": "virtualization/interfaces",
    
    # Tenancy
    "tenants": "tenancy/tenants",
    "tenant-groups": "tenancy/tenant-groups",
    "contacts": "tenancy/contacts",
    "contact-groups": "tenancy/contact-groups",
    "contact-roles": "tenancy/contact-roles",
    
    # VPN
    "ike-policies": "vpn/ike-policies",
    "ike-proposals": "vpn/ike-proposals",
    "ipsec-policies": "vpn/ipsec-policies",
    "ipsec-profiles": "vpn/ipsec-profiles",
    "ipsec-proposals": "vpn/ipsec-proposals",
    "l2vpns": "vpn/l2vpns",
    "tunnels": "vpn/tunnels",
    "tunnel-groups": "vpn/tunnel-groups",
    
    # Wireless
    "wireless-lans": "wireless/wireless-lans",
    "wireless-lan-groups": "wireless/wireless-lan-groups",
    "wireless-links": "wireless/wireless-links",

    # Extras
    "config-contexts": "extras/config-contexts",
    "custom-fields": "extras/custom-fields",
    "export-templates": "extras/export-templates",
    "image-attachments": "extras/image-attachments",
    "jobs": "extras/jobs",
    "saved-filters": "extras/saved-filters",
    "scripts": "extras/scripts",
    "tags": "extras/tags",
    "webhooks": "extras/webhooks",
}

NETBOX_OBJECT_TYPES_NETBOX4 = {
    # NetBox 4.x introduced Modules (dcim/modules + related models).
    "modules": "dcim/modules",
    "module-bays": "dcim/module-bays",
    "module-bay-templates": "dcim/module-bay-templates",
    # NetBox 4.x uses "module type profiles" (dcim/module-type-profiles). Some clients
    # may refer to these as "module-profiles"; keep an alias for convenience.
    "module-type-profiles": "dcim/module-type-profiles",
    "module-profiles": "dcim/module-type-profiles",
    "module-types": "dcim/module-types",
}

NETBOX_OBJECT_TYPES = dict(NETBOX_OBJECT_TYPES_BASE)

mcp = FastMCP("NetBox", log_level="DEBUG")
netbox = None

# Tools removed in read-only mode.
WRITE_TOOLS: list[str] = [
    "netbox_set_interface_mac",
    "netbox_create_object",
    "netbox_update_object",
    "netbox_delete_object",
    "netbox_bulk_create_objects",
    "netbox_bulk_update_objects",
    "netbox_bulk_delete_objects",
]

def _truthy_env(name: str, default: str = "false") -> bool:
    raw = os.getenv(name, default)
    return str(raw).strip().lower() in ("1", "true", "yes", "on")

def _maybe_wrap_results(value):
    """
    Some MCP clients/framework helpers treat a returned Python list as a list of
    "content blocks" rather than a JSON value, which can truncate/alter the payload.
    Safe-by-default: wrap list results unless explicitly disabled.
    """
    if _truthy_env("NETBOX_MCP_WRAP_LIST_RESULTS", "true") and isinstance(value, list):
        return {"count": len(value), "results": value}
    return value


CAPABILITIES = {
    # NetBox 4.x has dcim/mac-addresses; older versions may not.
    "has_mac_addresses_endpoint": False,
    # Older versions often accept setting interfaces.mac_address; NetBox 4.x makes it read-only.
    "interfaces_mac_address_writable": False,
    # NetBox 4.x uses interfaces.primary_mac_address referencing dcim/mac-addresses.
    "interfaces_primary_mac_address_writable": False,
}


def _options(endpoint: str):
    """
    Return OPTIONS payload for an endpoint, or None if the endpoint does not exist.
    Safe-by-default: callers must handle None by disabling features.
    """
    try:
        url = f"{netbox.api_url}/{endpoint.strip('/')}/"
        r = netbox.session.options(url, verify=netbox.verify_ssl)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _detect_capabilities() -> None:
    """
    Detect which NetBox API features are supported by the connected instance.
    This avoids breaking older NetBox versions (safe-by-default).
    """
    # Detect dcim/mac-addresses endpoint
    if _options("dcim/mac-addresses") is not None:
        CAPABILITIES["has_mac_addresses_endpoint"] = True
    else:
        # Hide unsupported endpoint from the object type mapping (avoid 404 surprises).
        NETBOX_OBJECT_TYPES.pop("mac-addresses", None)

    # Detect whether interfaces.mac_address is writable (older NetBox) vs read-only (NetBox 4.x).
    iface_opts = _options("dcim/interfaces")
    if not iface_opts:
        return

    actions = iface_opts.get("actions", {}) or {}
    # Prefer PATCH schema if present; fall back to POST schema.
    schema = actions.get("PATCH") or actions.get("POST") or {}

    mac_field = schema.get("mac_address") or {}
    if isinstance(mac_field, dict) and mac_field.get("read_only") is False:
        CAPABILITIES["interfaces_mac_address_writable"] = True

    primary_mac_field = schema.get("primary_mac_address") or {}
    if isinstance(primary_mac_field, dict) and primary_mac_field.get("read_only") is False:
        CAPABILITIES["interfaces_primary_mac_address_writable"] = True

def _detect_netbox_major_version(netbox_url: str, verify_ssl: bool, token: str | None = None):
    # /api/status/ may require auth depending on NetBox config; include token if provided.
    try:
        status_url = urljoin(netbox_url.rstrip("/") + "/", "api/status/")
        headers = {}
        if token:
            tok = token.strip()
            if tok.startswith(("Token ", "Bearer ")):
                headers["Authorization"] = tok
            elif tok.startswith("nbx_"):
                headers["Authorization"] = f"Bearer {tok}"
            else:
                headers["Authorization"] = f"Token {tok}"

        r = requests.get(status_url, timeout=5, verify=verify_ssl, headers=headers)
        r.raise_for_status()
        j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        ver = j.get("netbox-version") or j.get("netbox_version") or ""
        # Match leading major version (e.g. "4" from "4.5.1").
        m = re.match(r"^(\d+)", str(ver).strip())
        if m:
            return int(m.group(1))
    except Exception:
        return None
    return None

def _auto_detect_scheme(netbox_url: str) -> str:
    """
    Optionally adjust NETBOX_URL scheme based on reachability.

    Safe-by-default:
    - Disabled unless NETBOX_MCP_AUTO_SCHEME is enabled.
    - Only switches scheme if the current scheme is not reachable and the
      alternative one is.
    """
    mode = os.getenv("NETBOX_MCP_AUTO_SCHEME", "false").strip().lower()
    if mode not in ("1", "true", "yes", "on", "auto"):
        return netbox_url

    u = urlparse(netbox_url.strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        return netbox_url

    timeout = float(os.getenv("NETBOX_MCP_AUTO_SCHEME_TIMEOUT_SEC", "1.5"))
    host = u.hostname

    def _can_connect(port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    http_ok = _can_connect(80)
    https_ok = _can_connect(443)

    scheme = u.scheme
    if scheme == "https" and (not https_ok) and http_ok:
        scheme = "http"
    elif scheme == "http" and (not http_ok) and https_ok:
        scheme = "https"

    if scheme == u.scheme:
        return netbox_url

    return urlunparse((scheme, u.netloc, u.path or "/", "", u.query, u.fragment))


@mcp.tool()
def netbox_set_interface_mac(interface_id: int, mac_address: str):
    """
    Set the MAC address for an interface in a NetBox-version-aware way (safe-by-default).

    Behavior:
    - If interfaces.mac_address is writable, set it directly (older NetBox).
    - Else, if dcim/mac-addresses exists and interfaces.primary_mac_address is writable, create/assign a MAC object
      and set primary_mac_address (NetBox 4.x).
    """
    if not netbox:
        raise RuntimeError("NetBox client not initialized")

    if CAPABILITIES["interfaces_mac_address_writable"]:
        return netbox.update("dcim/interfaces", interface_id, {"mac_address": mac_address})

    if not CAPABILITIES["has_mac_addresses_endpoint"]:
        raise ValueError(
            "This NetBox instance does not support dcim/mac-addresses, and interfaces.mac_address is not writable. "
            "Cannot set MAC safely."
        )

    if not CAPABILITIES["interfaces_primary_mac_address_writable"]:
        raise ValueError(
            "This NetBox instance supports dcim/mac-addresses but interfaces.primary_mac_address is not writable. "
            "Cannot set MAC safely."
        )

    # Create or reuse the MAC address object, and assign it to the interface.
    existing = netbox.get("dcim/mac-addresses", params={"mac_address": mac_address})
    mac_obj = None
    if isinstance(existing, list) and existing:
        mac_obj = existing[0]
        # Ensure it's assigned to this interface.
        if (mac_obj.get("assigned_object_type") != "dcim.interface") or (mac_obj.get("assigned_object_id") != interface_id):
            mac_obj = netbox.update(
                "dcim/mac-addresses",
                mac_obj["id"],
                {"assigned_object_type": "dcim.interface", "assigned_object_id": interface_id},
            )
    else:
        mac_obj = netbox.create(
            "dcim/mac-addresses",
            {"mac_address": mac_address, "assigned_object_type": "dcim.interface", "assigned_object_id": interface_id},
        )

    # Prefer passing the ID (common NetBox behavior); fall back to nested object if needed.
    try:
        return netbox.update("dcim/interfaces", interface_id, {"primary_mac_address": mac_obj["id"]})
    except Exception:
        return netbox.update("dcim/interfaces", interface_id, {"primary_mac_address": {"id": mac_obj["id"]}})


@mcp.tool()
def netbox_get_objects(object_type: str, filters: dict):
    """
    Get objects from NetBox based on their type and filters
    Args:
        object_type: String representing the NetBox object type (e.g. "devices", "ip-addresses")
        filters: dict of filters to apply to the API call based on the NetBox API filtering options
    
    Valid object_type values:
    
    DCIM (Device and Infrastructure):
    - cables
    - console-ports
    - console-server-ports  
    - devices
    - device-bays
    - device-roles
    - device-types
    - front-ports
    - interfaces
    - inventory-items
    - locations
    - manufacturers
    - platforms
    - power-feeds
    - power-outlets
    - power-panels
    - power-ports
    - racks
    - rack-reservations
    - rack-roles
    - regions
    - sites
    - site-groups
    - virtual-chassis
    
    IPAM (IP Address Management):
    - asns
    - asn-ranges
    - aggregates 
    - fhrp-groups
    - ip-addresses
    - ip-ranges
    - prefixes
    - rirs
    - roles
    - route-targets
    - services
    - vlans
    - vlan-groups
    - vrfs
    
    Circuits:
    - circuits
    - circuit-types
    - circuit-terminations
    - providers
    - provider-networks
    
    Virtualization:
    - clusters
    - cluster-groups
    - cluster-types
    - virtual-machines
    - vm-interfaces
    
    Tenancy:
    - tenants
    - tenant-groups
    - contacts
    - contact-groups
    - contact-roles
    
    VPN:
    - ike-policies
    - ike-proposals
    - ipsec-policies
    - ipsec-profiles
    - ipsec-proposals
    - l2vpns
    - tunnels
    - tunnel-groups
    
    Wireless:
    - wireless-lans
    - wireless-lan-groups
    - wireless-links

    NetBox 4.x only:
    - modules
    - module-bays
    - module-profiles
    - module-types
    
    See NetBox API documentation for filtering options for each object type.
    """
    # Validate object_type exists in mapping
    if object_type not in NETBOX_OBJECT_TYPES:
        valid_types = "\n".join(f"- {t}" for t in sorted(NETBOX_OBJECT_TYPES.keys()))
        raise ValueError(f"Invalid object_type. Must be one of:\n{valid_types}")
        
    # Get API endpoint from mapping
    endpoint = NETBOX_OBJECT_TYPES[object_type]
        
    # Make API call
    results = netbox.get(endpoint, params=filters)
    return _maybe_wrap_results(results)

@mcp.tool()
def netbox_get_object_by_id(object_type: str, object_id: int):
    """
    Get detailed information about a specific NetBox object by its ID.
    
    Args:
        object_type: String representing the NetBox object type (e.g. "devices", "ip-addresses")
        object_id: The numeric ID of the object
    
    Returns:
        Complete object details
    """
    # Validate object_type exists in mapping
    if object_type not in NETBOX_OBJECT_TYPES:
        valid_types = "\n".join(f"- {t}" for t in sorted(NETBOX_OBJECT_TYPES.keys()))
        raise ValueError(f"Invalid object_type. Must be one of:\n{valid_types}")
        
    # Get API endpoint from mapping
    endpoint = f"{NETBOX_OBJECT_TYPES[object_type]}/{object_id}"
    
    return netbox.get(endpoint)

@mcp.tool()
def netbox_search_objects(query: str, object_type: str = None,
                          fields: list = None, limit: int = 50):
    """Search NetBox using the ?q= query parameter.

    Args:
        query: search string (matched against object names, descriptions, etc.)
        object_type: optional — restrict to a single type (e.g. "devices",
                     "virtual-machines"). If None, searches a default set of
                     common types and returns a flat list with `__object_type__`
                     injected on each result.
        fields: optional list of fields to return per match (RECOMMENDED for token
                efficiency — e.g. ["id", "name", "status"]).
        limit: max results per type (default 50).

    Returns:
        List of matching objects. When object_type is None, each dict has an
        extra `__object_type__` key indicating which type matched.
    """
    DEFAULT_TYPES = [
        "devices", "virtual-machines", "ip-addresses", "prefixes",
        "vlans", "sites", "interfaces",
    ]
    types_to_search = [object_type] if object_type else DEFAULT_TYPES

    results = []
    for t in types_to_search:
        if t not in NETBOX_OBJECT_TYPES:
            if object_type:
                raise ValueError(f"Invalid object_type: {t}")
            continue

        params = {"q": query, "limit": limit}
        if fields:
            params["fields"] = ",".join(fields)
        try:
            objs = netbox.get(NETBOX_OBJECT_TYPES[t], params=params)
        except Exception:
            if object_type:
                raise
            continue

        if not object_type:
            for o in objs:
                o["__object_type__"] = t
        results.extend(objs)

    return results

@mcp.tool()
def netbox_get_changelogs(filters: dict):
    """
    Get object change records (changelogs) from NetBox based on filters.
    
    Args:
        filters: dict of filters to apply to the API call based on the NetBox API filtering options
    
    Returns:
        List of changelog objects matching the specified filters
    
    Filtering options include:
    - user_id: Filter by user ID who made the change
    - user: Filter by username who made the change
    - changed_object_type_id: Filter by ContentType ID of the changed object
    - changed_object_id: Filter by ID of the changed object
    - object_repr: Filter by object representation (usually contains object name)
    - action: Filter by action type (created, updated, deleted)
    - time_before: Filter for changes made before a given time (ISO 8601 format)
    - time_after: Filter for changes made after a given time (ISO 8601 format)
    - q: Search term to filter by object representation

    Example:
    To find all changes made to a specific device with ID 123:
    {"changed_object_type_id": "dcim.device", "changed_object_id": 123}
    
    To find all deletions in the last 24 hours:
    {"action": "delete", "time_after": "2023-01-01T00:00:00Z"}
    
    Each changelog entry contains:
    - id: The unique identifier of the changelog entry
    - user: The user who made the change
    - user_name: The username of the user who made the change
    - request_id: The unique identifier of the request that made the change
    - action: The type of action performed (created, updated, deleted)
    - changed_object_type: The type of object that was changed
    - changed_object_id: The ID of the object that was changed
    - object_repr: String representation of the changed object
    - object_data: The object's data after the change (null for deletions)
    - object_data_v2: Enhanced data representation
    - prechange_data: The object's data before the change (null for creations)
    - postchange_data: The object's data after the change (null for deletions)
    - time: The timestamp when the change was made
    """
    endpoint = "core/object-changes"
    
    # Make API call
    results = netbox.get(endpoint, params=filters)
    return _maybe_wrap_results(results)

@mcp.tool()
def netbox_create_object(object_type: str, data: dict):
    """
    Create a new object in NetBox.
    
    Args:
        object_type: String representing the NetBox object type (e.g. "devices", "ip-addresses")
        data: Dict containing the object data to create
        
    Returns:
        The created object as a dict
        
    Example:
    To create a new site:
    netbox_create_object("sites", {
        "name": "New Site",
        "slug": "new-site", 
        "status": "active"
    })
    
    To create a new device:
    netbox_create_object("devices", {
        "name": "new-device",
        "device_type": 1,  # ID of device type
        "site": 1,         # ID of site
        "role": 1,         # ID of device role
        "status": "active"
    })
    """
    # Validate object_type exists in mapping
    if object_type not in NETBOX_OBJECT_TYPES:
        valid_types = "\n".join(f"- {t}" for t in sorted(NETBOX_OBJECT_TYPES.keys()))
        raise ValueError(f"Invalid object_type. Must be one of:\n{valid_types}")
        
    # Get API endpoint from mapping
    endpoint = NETBOX_OBJECT_TYPES[object_type]
        
    # Make API call
    return netbox.create(endpoint, data)

@mcp.tool()
def netbox_update_object(object_type: str, object_id: int, data: dict):
    """
    Update an existing object in NetBox.
    
    Args:
        object_type: String representing the NetBox object type (e.g. "devices", "ip-addresses")
        object_id: The numeric ID of the object to update
        data: Dict containing the object data to update (only changed fields needed)
        
    Returns:
        The updated object as a dict
        
    Example:
    To update a site's description:
    netbox_update_object("sites", 1, {"description": "Updated description"})
    
    To change a device's status:
    netbox_update_object("devices", 5, {"status": "offline"})
    """
    # Validate object_type exists in mapping
    if object_type not in NETBOX_OBJECT_TYPES:
        valid_types = "\n".join(f"- {t}" for t in sorted(NETBOX_OBJECT_TYPES.keys()))
        raise ValueError(f"Invalid object_type. Must be one of:\n{valid_types}")
        
    # Get API endpoint from mapping
    endpoint = NETBOX_OBJECT_TYPES[object_type]
        
    # Make API call
    return netbox.update(endpoint, object_id, data)

@mcp.tool()
def netbox_delete_object(object_type: str, object_id: int):
    """
    Delete an object from NetBox.
    
    Args:
        object_type: String representing the NetBox object type (e.g. "devices", "ip-addresses")
        object_id: The numeric ID of the object to delete
        
    Returns:
        True if deletion was successful
        
    WARNING: This permanently deletes the object and cannot be undone!
    
    Example:
    To delete a device:
    netbox_delete_object("devices", 5)
    
    To delete an IP address:
    netbox_delete_object("ip-addresses", 123)
    """
    # Validate object_type exists in mapping
    if object_type not in NETBOX_OBJECT_TYPES:
        valid_types = "\n".join(f"- {t}" for t in sorted(NETBOX_OBJECT_TYPES.keys()))
        raise ValueError(f"Invalid object_type. Must be one of:\n{valid_types}")
        
    # Get API endpoint from mapping
    endpoint = NETBOX_OBJECT_TYPES[object_type]
        
    # Make API call - this will raise an exception if it fails
    success = netbox.delete(endpoint, object_id)
    
    if success:
        return {"success": True, "message": f"Successfully deleted {object_type} with ID {object_id}"}
    else:
        return {"success": False, "message": f"Failed to delete {object_type} with ID {object_id}"}

@mcp.tool()
def netbox_bulk_create_objects(object_type: str, data: list):
    """
    Create multiple objects in NetBox in a single request.
    
    Args:
        object_type: String representing the NetBox object type (e.g. "devices", "ip-addresses")
        data: List of dicts containing the object data to create
        
    Returns:
        List of created objects
        
    Example:
    To create multiple sites:
    netbox_bulk_create_objects("sites", [
        {"name": "Site A", "slug": "site-a", "status": "active"},
        {"name": "Site B", "slug": "site-b", "status": "active"}
    ])
    """
    # Validate object_type exists in mapping
    if object_type not in NETBOX_OBJECT_TYPES:
        valid_types = "\n".join(f"- {t}" for t in sorted(NETBOX_OBJECT_TYPES.keys()))
        raise ValueError(f"Invalid object_type. Must be one of:\n{valid_types}")
        
    # Get API endpoint from mapping
    endpoint = NETBOX_OBJECT_TYPES[object_type]
        
    # Make API call
    results = netbox.bulk_create(endpoint, data)
    return _maybe_wrap_results(results)

@mcp.tool()
def netbox_bulk_update_objects(object_type: str, data: list):
    """
    Update multiple objects in NetBox in a single request.
    
    Args:
        object_type: String representing the NetBox object type (e.g. "devices", "ip-addresses")
        data: List of dicts containing the object data to update (must include "id" field)
        
    Returns:
        List of updated objects
        
    Example:
    To update multiple devices:
    netbox_bulk_update_objects("devices", [
        {"id": 1, "status": "offline"},
        {"id": 2, "status": "maintenance"}
    ])
    """
    # Validate object_type exists in mapping
    if object_type not in NETBOX_OBJECT_TYPES:
        valid_types = "\n".join(f"- {t}" for t in sorted(NETBOX_OBJECT_TYPES.keys()))
        raise ValueError(f"Invalid object_type. Must be one of:\n{valid_types}")
        
    # Get API endpoint from mapping
    endpoint = NETBOX_OBJECT_TYPES[object_type]
        
    # Make API call
    results = netbox.bulk_update(endpoint, data)
    return _maybe_wrap_results(results)

@mcp.tool()
def netbox_bulk_delete_objects(object_type: str, object_ids: list):
    """
    Delete multiple objects from NetBox in a single request.
    
    Args:
        object_type: String representing the NetBox object type (e.g. "devices", "ip-addresses")  
        object_ids: List of numeric IDs to delete
        
    Returns:
        Success status
        
    WARNING: This permanently deletes the objects and cannot be undone!
    
    Example:
    To delete multiple devices:
    netbox_bulk_delete_objects("devices", [5, 6, 7])
    """
    # Validate object_type exists in mapping
    if object_type not in NETBOX_OBJECT_TYPES:
        valid_types = "\n".join(f"- {t}" for t in sorted(NETBOX_OBJECT_TYPES.keys()))
        raise ValueError(f"Invalid object_type. Must be one of:\n{valid_types}")
        
    # Get API endpoint from mapping
    endpoint = NETBOX_OBJECT_TYPES[object_type]
        
    # Make API call
    success = netbox.bulk_delete(endpoint, object_ids)
    
    if success:
        return {"success": True, "message": f"Successfully deleted {len(object_ids)} {object_type} objects"}
    else:
        return {"success": False, "message": f"Failed to delete {object_type} objects"}

def main():
    global netbox

    # Load NetBox configuration from environment variables
    netbox_url = os.getenv("NETBOX_URL")
    netbox_token = os.getenv("NETBOX_TOKEN")
    verify_ssl_raw = os.getenv("NETBOX_VERIFY_SSL", "true")
    verify_ssl = str(verify_ssl_raw).strip().lower() not in ("0", "false", "no", "off")

    if not netbox_url or not netbox_token:
        raise ValueError("NETBOX_URL and NETBOX_TOKEN environment variables must be set")

    netbox_url = _auto_detect_scheme(netbox_url)

    # Initialize NetBox client
    netbox = NetBoxRestClient(url=netbox_url, token=netbox_token, verify_ssl=verify_ssl)

    # Version-gate NetBox 4-only endpoints so we don't advertise unsupported object
    # types to older NetBox instances. Override via env if needed.
    gate_mode = os.getenv("NETBOX_MCP_ENABLE_NETBOX4_OBJECTS", "auto").strip().lower()
    major = _detect_netbox_major_version(netbox_url, verify_ssl=verify_ssl, token=netbox_token) if gate_mode == "auto" else None
    enable_netbox4 = (gate_mode == "true") or (gate_mode == "1") or (gate_mode == "yes") or (gate_mode == "on") or (major is not None and major >= 4)
    if (not enable_netbox4) and gate_mode == "auto":
        # Safe fallback: if /api/status can't be read, probe an actual 4.x-only endpoint.
        enable_netbox4 = _options("dcim/modules") is not None
    if enable_netbox4:
        NETBOX_OBJECT_TYPES.update(NETBOX_OBJECT_TYPES_NETBOX4)
    _detect_capabilities()

    # Read-only mode: remove write tools
    if _truthy_env("NETBOX_READ_ONLY") and WRITE_TOOLS:
        for name in WRITE_TOOLS:
            mcp.remove_tool(name)
        import logging
        logging.info("Read-only mode: %d write tools removed", len(WRITE_TOOLS))

    mcp.run(transport="stdio")

if __name__ == "__main__":
    main()
