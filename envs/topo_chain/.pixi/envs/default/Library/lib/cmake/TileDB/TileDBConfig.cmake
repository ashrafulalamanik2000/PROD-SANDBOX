#
# This file attempts to locate the TileDB library. If found, the following
# imported targets are created:
#   - TileDB::tiledb (points to either a static or shared library)
#   - TileDB::tiledb_shared (if TileDB is built as a shared library)
#   - TileDB::tiledb_static (if TileDB is built as a static library)
# And the following variables are defined:
#   - TILEDB_FOUND
#   - TileDB_FOUND
#


####### Expanded from @PACKAGE_INIT@ by configure_package_config_file() #######
####### Any changes to this file will be overwritten by the next CMake run ####
####### The input file was Config.cmake.in                            ########

get_filename_component(PACKAGE_PREFIX_DIR "${CMAKE_CURRENT_LIST_DIR}/../../../" ABSOLUTE)

macro(set_and_check _var _file)
  set(${_var} "${_file}")
  if(NOT EXISTS "${_file}")
    message(FATAL_ERROR "File or directory ${_file} referenced by variable ${_var} does not exist !")
  endif()
endmacro()

macro(check_required_components _NAME)
  foreach(comp ${${_NAME}_FIND_COMPONENTS})
    if(NOT ${_NAME}_${comp}_FOUND)
      if(${_NAME}_FIND_REQUIRED_${comp})
        set(${_NAME}_FOUND FALSE)
      endif()
    endif()
  endforeach()
endmacro()

####################################################################################

if(NOT ON) # NOT BUILD_SHARED_LIBS
  include(CMakeFindDependencyMacro)
  find_dependency(Threads)
  find_dependency(Blosc2)
  find_dependency(BZip2)
  find_dependency(lz4)
  find_dependency(spdlog)
  find_dependency(ZLIB)
  find_dependency(zstd)
  if(NOT WIN32)
    find_dependency(OpenSSL)
  endif()
  if(ON) # TILEDB_S3
    find_dependency(AWSSDK COMPONENTS identity-management;sts;s3)
  endif()
  if(ON) # TILEDB_AZURE
    find_dependency(azure-identity-cpp)
    find_dependency(azure-storage-blobs-cpp)
    find_dependency(azure-storage-files-datalake-cpp)
  endif()
  if(ON) # TILEDB_GCS
    find_dependency(google_cloud_cpp_storage)
  endif()
  if(ON) # TILEDB_SERIALIZATION
    find_dependency(CapnProto)
    find_dependency(CURL)
  endif()
  if(ON) # TILEDB_WEBP
    find_dependency(WebP)
  endif()
endif()

include("${CMAKE_CURRENT_LIST_DIR}/TileDBTargets.cmake")
check_required_components("TileDB")

if(NOT TARGET TileDB::tiledb)
  if(TARGET TileDB::tiledb_shared)
    add_library(TileDB::tiledb INTERFACE IMPORTED)
    set_target_properties(TileDB::tiledb PROPERTIES INTERFACE_LINK_LIBRARIES TileDB::tiledb_shared)
  elseif(TARGET TileDB::tiledb_static)
    add_library(TileDB::tiledb INTERFACE IMPORTED)
    set_target_properties(TileDB::tiledb PROPERTIES INTERFACE_LINK_LIBRARIES TileDB::tiledb_static)
  endif()
endif()

# Define a convenience all-caps variable
if (NOT DEFINED TILEDB_FOUND)
  if (TARGET TileDB::tiledb)
    set(TILEDB_FOUND TRUE)
  else()
    set(TILEDB_FOUND FALSE)
  endif()
endif()
