#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "SuiteSparse::KLU_CHOLMOD" for configuration "Release"
set_property(TARGET SuiteSparse::KLU_CHOLMOD APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(SuiteSparse::KLU_CHOLMOD PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/klu_cholmod.lib"
  IMPORTED_LINK_DEPENDENT_LIBRARIES_RELEASE "SuiteSparse::CHOLMOD;SuiteSparse::KLU"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/klu_cholmod.dll"
  )

list(APPEND _cmake_import_check_targets SuiteSparse::KLU_CHOLMOD )
list(APPEND _cmake_import_check_files_for_SuiteSparse::KLU_CHOLMOD "${_IMPORT_PREFIX}/lib/klu_cholmod.lib" "${_IMPORT_PREFIX}/bin/klu_cholmod.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
