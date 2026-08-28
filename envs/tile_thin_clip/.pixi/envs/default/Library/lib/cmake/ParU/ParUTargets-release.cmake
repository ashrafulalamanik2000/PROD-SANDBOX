#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "SuiteSparse::ParU" for configuration "Release"
set_property(TARGET SuiteSparse::ParU APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(SuiteSparse::ParU PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/paru.lib"
  IMPORTED_LINK_DEPENDENT_LIBRARIES_RELEASE "SuiteSparse::SuiteSparseConfig;SuiteSparse::UMFPACK"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/paru.dll"
  )

list(APPEND _cmake_import_check_targets SuiteSparse::ParU )
list(APPEND _cmake_import_check_files_for_SuiteSparse::ParU "${_IMPORT_PREFIX}/lib/paru.lib" "${_IMPORT_PREFIX}/bin/paru.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
