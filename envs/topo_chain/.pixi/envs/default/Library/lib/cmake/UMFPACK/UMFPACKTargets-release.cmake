#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "SuiteSparse::UMFPACK" for configuration "Release"
set_property(TARGET SuiteSparse::UMFPACK APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(SuiteSparse::UMFPACK PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/umfpack.lib"
  IMPORTED_LINK_DEPENDENT_LIBRARIES_RELEASE "SuiteSparse::SuiteSparseConfig;SuiteSparse::AMD;SuiteSparse::CHOLMOD"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/umfpack.dll"
  )

list(APPEND _cmake_import_check_targets SuiteSparse::UMFPACK )
list(APPEND _cmake_import_check_files_for_SuiteSparse::UMFPACK "${_IMPORT_PREFIX}/lib/umfpack.lib" "${_IMPORT_PREFIX}/bin/umfpack.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
