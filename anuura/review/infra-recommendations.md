## Infrastructure recommendations

1. Data management. We need a local cache of data and models that are commonly used by students. This will help reduce repeated downloads, setup tools that they can reuse making iterations easier.
2. Storage management. It is unclear how we can make this cache accessible to Ada and Turing immediately but one suggestion is this:
   1. Buy a NAS and use that as the central storage for all of LTRC and replicate it to dedicated spaces mounted on Ada and Turing so that it can be used across all compute.
3. Dependency management. We also need better dependency management to reduce turnaround times and improve student throughputs.
   1. Containerisation with appropriate libraries pre-installed and maintained so that the models are readily accessible through the compute is one option. This requires a good sysadmin but is a worthwhile investment for the team offering more flexibility in quickly reusing both available compute setups but also external cloud environments.
4. Compute management. There are two kinds of workloads,
   1. Dev-mode: where students are developing and debugging code. This is slow and needs hardware on standby only to test code while they modify it.
   2. Burst-mode: where the code is run to finish the job. We should be movable (to easily run this on any compute, internal or external) and scalable (to leverage more hardware when available).

