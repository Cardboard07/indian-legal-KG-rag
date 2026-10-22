# LTRC NAS storage (gaur)

`gaur` is a Synology NAS running DSM 7.3 for lab-wide storage. 
The web UI is at `gaur.iiit.ac.in`. The lab compute nodes will mount it over NFS once they are attached.
It has 50TB from 5x10TB 7200RPM HDD disks with RAID 5 configuration giving us about 40TB usable storage space effectively. 
It is connected to others in Himalaya server room through a 10Gbps network switch and rest-of-IIIT through the 1Gbps bottleneck.

## Accounts

1. Accounts live in an LDAP directory hosted on gaur itself (LDAP Server package, base DN `dc=gaur,dc=iiit,dc=ac,dc=in`). gaur is joined to its own directory, which is what lets LDAP users log into DSM and hold folder permissions.
2. To onboard someone, create their user in LDAP Server > Manage Users. A private home folder is created automatically (home folders for LDAP users are a separate toggle, enabled in Control Panel > Domain/LDAP). They should log in at <gaur.iiit.ac.in> and change their password.
3. The compute nodes will authenticate against this same directory via sssd. NFS enforces permissions by numeric uid, so the NAS and the nodes must agree on uids, and the common directory guarantees that.

## Groups


| Group             | Purpose                                                                                 |
| ----------------- | --------------------------------------------------------------------------------------- |
| `users` (default) | every lab member is in it, grants read on `hf_cache` and read/write on `shared`         |
| `cache-writers`   | read/write on `hf_cache`, membership is temporary and handed out for pulling new models |
| `research`        | no permissions on the NAS, reserved for GPU quota management on the cluster             |


## Shared folders


| Folder         | Access                               | Purpose                                                        |
| -------------- | ------------------------------------ | -------------------------------------------------------------- |
| `homes/<user>` | private                              | personal space, created automatically with the account         |
| `shared`       | everyone, read/write                 | common area for the lab, like `/tmp` on ada except it persists |
| `hf_cache`     | everyone read, `cache-writers` write | shared HuggingFace cache for models and datasets               |


## HF cache

1. Read-only for everyone so nothing the lab depends on gets deleted by accident. On compute nodes `HF_HOME` and `TORCH_HOME` will point here, so anything already pulled loads without re-downloading.
2. When someone needs to pull a new model or dataset, add them to `cache-writers` and remove them once they are done.
3. Large data on ada/turing that will be moved into the cache is listed in [old_stores.md](old_stores.md).

## Snapshots and recovery

1. `shared` and `homes` are snapshotted hourly with Snapshot Replication, using the default retention (all snapshots for 1 day, then 24 hourly, 7 daily, 2 weekly, 1 monthly, and the 5 latest always kept).
2. The recycle bin is enabled on `shared` and catches deletes made through the web UI or SMB. It is disabled on `hf_cache`.
3. Deletes over NFS bypass the recycle bin entirely, snapshots are the only recovery in that case.

## NFS and compute nodes (pending)

1. The NFS service is enabled, capped at v4.1, with root squash on, so root on a compute node cannot bypass permissions.
2. Export rules are per node and will be added once the node hostnames or IPs are known.
3. After mounting, `HF_HOME` and `TORCH_HOME` on the nodes point at the cache path. The exact mount paths will be documented here then.

