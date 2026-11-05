// SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// Compile against installed vendor headers before implementing a Python ABI.
#include <iostream>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>
#ifdef METAX
#include <mc_runtime_api.h>
#else
#include <acl/acl.h>
#endif
#define CHECK(call) do { int rc = (call); std::cout << #call << " rc=" << rc << std::endl; if (rc) throw std::runtime_error(#call); } while (0)
int main(int argc, char **argv) {
  if (argc != 3) return 2;
  int device = std::stoi(argv[1]), mode = std::stoi(argv[2]);
  size_t size = 0;
  bool mapped = false;
#ifdef METAX
  mcMemGenericAllocationHandle handle = 0;
  mcDeviceptr_t va = 0;
#else
  aclrtDrvMemHandle handle = nullptr;
  void *va = nullptr;
#endif
  int result = 0;
  try {
#ifdef METAX
    CHECK(mcSetDevice(device));
    mcMemAllocationProp prop{};
    prop.type = mcMemAllocationTypePinned;
    prop.location = {mcMemLocationTypeDevice, device};
    prop.requestedHandleTypes = mcMemHandleTypePosixFileDescriptor;
    CHECK(mcMemGetAllocationGranularity(&size, &prop, MC_MEM_ALLOC_GRANULARITY_MINIMUM));
    std::cout << "granularity=" << size << " prop_size=" << sizeof(prop) << " access_size=" << sizeof(mcMemAccessDesc) << std::endl;
    CHECK(mcMemCreate(&handle, size, &prop, 0));
    CHECK(mcMemAddressReserve(&va, size, size, 0, 0));
    CHECK(mcMemMap(va, size, 0, handle, 0)); mapped = true;
    mcMemAccessDesc desc{}; desc.location = prop.location;
    desc.flags = mcMemAccessFlagsProtReadWrite;
    if (mode != 2) CHECK(mcMemSetAccess(va, size, &desc, 1));
    desc.flags = mcMemAccessFlagsProtRead;
    CHECK(mcMemSetAccess(va, size, &desc, 1));
    unsigned long long flags = 0;
    CHECK(mcMemGetAccess(&flags, &prop.location, va));
    std::cout << "read_access=" << flags << std::endl;
    if (flags != mcMemAccessFlagsProtRead) result = 4;
    int fd = -1;
    CHECK(mcMemExportToShareableHandle(&fd, handle, mcMemHandleTypePosixFileDescriptor, 0));
    struct stat st{};
    int stat_rc = fstat(fd, &st); close(fd);
    if (stat_rc) throw std::runtime_error("export is not an FD");
    std::cout << "posix_fd=true" << std::endl;
#else
    CHECK(aclInit(nullptr));
    CHECK(aclrtSetDevice(device));
    aclrtPhysicalMemProp prop{};
    if (mode < 0 || mode > 3) throw std::runtime_error("unsupported Ascend probe mode");
    prop.handleType = mode == 1 ? ACL_MEM_HANDLE_TYPE_POSIX : ACL_MEM_HANDLE_TYPE_NONE;
    prop.allocationType = ACL_MEM_ALLOCATION_TYPE_PINNED;
    prop.memAttr = ACL_HBM_MEM_NORMAL;
    prop.location = {static_cast<uint32_t>(device), ACL_MEM_LOCATION_TYPE_DEVICE};
    CHECK(aclrtMemGetAllocationGranularity(&prop, ACL_RT_MEM_ALLOC_GRANULARITY_MINIMUM, &size));
    std::cout << "granularity=" << size << " prop_size=" << sizeof(prop) << " access_size=" << sizeof(aclrtMemAccessDesc) << std::endl;
    CHECK(aclrtMallocPhysical(&handle, size, &prop, 0));
    CHECK(aclrtReserveMemAddress(&va, size, 0, nullptr, 0));
    CHECK(aclrtMapMem(va, size, 0, handle, 0)); mapped = true;
    aclrtMemAccessDesc desc{}; desc.location = prop.location;
    desc.flags = ACL_RT_MEM_ACCESS_FLAGS_READWRITE;
    int rw = 0;
    std::cout << "access_sequence=" << (mode == 2 ? "fresh-ro" : mode == 3 ? "rw-remap-ro" : "rw-ro") << std::endl;
    if (mode != 2) {
      rw = aclrtMemSetAccess(va, size, &desc, 1);
      std::cout << "set_access_rw rc=" << rw << std::endl;
    }
    if (mode == 3) {
      CHECK(aclrtSynchronizeDevice());
      CHECK(aclrtUnmapMem(va)); mapped = false;
      CHECK(aclrtMapMem(va, size, 0, handle, 0)); mapped = true;
    }
    desc.flags = ACL_RT_MEM_ACCESS_FLAGS_READ;
    int ro = aclrtMemSetAccess(va, size, &desc, 1);
    std::cout << "set_access_ro rc=" << ro << std::endl;
    uint64_t flags = 0;
    int query = aclrtMemGetAccess(va, &prop.location, &flags);
    std::cout << "get_access rc=" << query << " read_access=" << flags << std::endl;
    if (rw || ro || query || flags != ACL_RT_MEM_ACCESS_FLAGS_READ) result = 4;
    uint64_t share = 0;
    CHECK(aclrtMemExportToShareableHandle(handle, prop.handleType, 0, &share));
    std::cout << "exported_handle_type=" << int(prop.handleType) << std::endl;
    if (mode == 1) {
      struct stat st{}; int fd = static_cast<int>(share);
      if (share > INT32_MAX || fstat(fd, &st)) throw std::runtime_error("POSIX export is not an FD");
      close(fd); std::cout << "posix_fd=true" << std::endl;
    }
#endif
  } catch (const std::exception &e) {
    std::cout << "probe_error=" << e.what() << std::endl; result = 1;
  }
#ifdef METAX
  if (mapped) { int rc = mcMemUnmap(va, size); std::cout << "unmap_rc=" << rc << std::endl; if (rc) return 3; }
  if (va) { int rc = mcMemAddressFree(va, size); std::cout << "address_free_rc=" << rc << std::endl; if (rc) result = 3; }
  if (handle) { int rc = mcMemRelease(handle); std::cout << "release_rc=" << rc << std::endl; if (rc) result = 3; }
#else
  if (mapped) { int rc = aclrtUnmapMem(va); std::cout << "unmap_rc=" << rc << std::endl; if (rc) return 3; }
  if (va) { int rc = aclrtReleaseMemAddress(va); std::cout << "address_free_rc=" << rc << std::endl; if (rc) result = 3; }
  if (handle) { int rc = aclrtFreePhysical(handle); std::cout << "release_rc=" << rc << std::endl; if (rc) result = 3; }
#endif
  return result;
}
