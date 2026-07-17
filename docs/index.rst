APM Python SDK
==============

Python SDK for `Synology ActiveProtect Manager <https://www.synology.com/products/ActiveProtectAppliance>`_.

**Installation**

.. code-block:: bash

   pip install synology-apm-sdk

**Quick start**

.. code-block:: python

   import asyncio
   from synology_apm.sdk import APMClient

   async def main():
       async with APMClient("apm.corp.com", "admin", "password") as apm:
           workloads = await apm.machine.workloads.list()
           for wl in workloads:
               print(wl.name, wl.status)

   asyncio.run(main())

.. toctree::
   :maxdepth: 2
   :caption: Client

   api/synology_apm.sdk.client

.. toctree::
   :maxdepth: 2
   :caption: Configuration

   api/synology_apm.sdk.config

.. toctree::
   :maxdepth: 2
   :caption: Exceptions & Enums

   api/synology_apm.sdk.exceptions
   api/synology_apm.sdk.enums

.. toctree::
   :maxdepth: 2
   :caption: Models

   api/synology_apm.sdk.models.workload
   api/synology_apm.sdk.models.location
   api/synology_apm.sdk.models.activity
   api/synology_apm.sdk.models.protection_plan
   api/synology_apm.sdk.models.retirement_plan
   api/synology_apm.sdk.models.tiering_plan
   api/synology_apm.sdk.models.version
   api/synology_apm.sdk.models.backup_server
   api/synology_apm.sdk.models.hypervisor
   api/synology_apm.sdk.models.log
   api/synology_apm.sdk.models.saas
   api/synology_apm.sdk.models.m365_auto_backup_rule
   api/synology_apm.sdk.models.system
   api/synology_apm.sdk.models.remote_storage

.. toctree::
   :maxdepth: 2
   :caption: Collections

   api/synology_apm.sdk.collections.machine
   api/synology_apm.sdk.collections.m365
   api/synology_apm.sdk.collections.m365_auto_backup_rule
   api/synology_apm.sdk.collections.protection_plans
   api/synology_apm.sdk.collections.retirement_plans
   api/synology_apm.sdk.collections.tiering_plans
   api/synology_apm.sdk.collections.activities
   api/synology_apm.sdk.collections.backup_servers
   api/synology_apm.sdk.collections.hypervisors
   api/synology_apm.sdk.collections.logs
   api/synology_apm.sdk.collections.m365_mail_export
   api/synology_apm.sdk.collections.saas
   api/synology_apm.sdk.collections.system
   api/synology_apm.sdk.collections.remote_storages
