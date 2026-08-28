// This file was programatically generated from 'D:/bld/bld/rattler-build_libpdal-core_1785772664/work/pdal/Dimension.json.
// Do not edit directly.

#pragma once

#include <string>
#include <vector>

#include <pdal/DimUtil.hpp>
#include <pdal/pdal_types.hpp>
#include <pdal/util/Utils.hpp>

namespace pdal
{
namespace Dimension
{

enum class Id
{
    Unknown,
    X, ///< \brief X coordinate
       ///< \default Double
    Y, ///< \brief Y coordinate
       ///< \default Double
    Z, ///< \brief Z coordinate
       ///< \default Double
    W, ///< \brief W coordinate
       ///< \default Double
    Intensity, ///< \brief Representation of the pulse return magnitude
               ///< \default Unsigned16
    Amplitude, ///< \brief This is the ratio of the received power to the power received at the detection limit expressed in dB
               ///< \default Float
    Reflectance, ///< \brief Ratio of the received power to the power that would be received from a white diffuse target at the same distance expressed in dB. The reflectance represents a range independent property of the target.  The surface normal of this target is assumed to be in parallel to the laser beam direction.
                 ///< \default Float
    ReturnNumber, ///< \brief Pulse return number for a given output pulse. A given output laser pulse can have many returns, and they must be marked in order, starting with 1
                  ///< \default Unsigned8
    NumberOfReturns, ///< \brief Total number of returns for a given pulse.
                     ///< \default Unsigned8
    ScanDirectionFlag, ///< \brief Direction at which the scanner mirror was traveling at the time of the output pulse. A value of 1 is a positive scan direction, and a bit value of 0 is a negative scan direction, where positive scan direction is a scan moving from the left side of the in-track direction to the right side and negative the opposite
                       ///< \default Unsigned8
    EdgeOfFlightLine, ///< \brief Indicates the end of scanline before a direction change with a value of 1 - 0 otherwise
                      ///< \default Unsigned8
    Classification, ///< \brief ASPRS classification.  0 for no classification.  See LAS specification for details.
                    ///< \default Unsigned8
    ScanAngleRank, ///< \brief Angle degree at which the laser point was output from the system, including the roll of the aircraft.  The scan angle is based on being nadir, and -90 the left side of the aircraft in the direction of flight
                   ///< \default Float
    UserData, ///< \brief Unspecified user data
              ///< \default Unsigned8
    PointSourceId, ///< \brief File source ID from which the point originated.  Zero indicates that the point originated in the current file
                   ///< \default Unsigned16
    Red, ///< \brief Red image channel value
         ///< \default Unsigned16
    Green, ///< \brief Green image channel value
           ///< \default Unsigned16
    Blue, ///< \brief Blue image channel value
          ///< \default Unsigned16
    GpsTime, ///< \brief GPS time that the point was acquired
             ///< \default Double
    InternalTime, ///< \brief Scanner's internal time when the point was acquired, in seconds
                  ///< \default Double
    OffsetTime, ///< \brief Milliseconds from first acquired point
                ///< \default Unsigned32
    IsPpsLocked, ///< \brief The external PPS signal was found to be synchronized at the time of the current laser shot.
                 ///< \default Unsigned8
    StartPulse, ///< \brief Relative pulse signal strength
                ///< \default Signed32
    ReflectedPulse, ///< \brief Relative reflected pulse signal strength
                    ///< \default Signed32
    Pdop, ///< \brief GPS PDOP (dilution of precision)
          ///< \default Float
    Pitch, ///< \brief Pitch in degrees
           ///< \default Float
    Roll, ///< \brief Roll in degrees
          ///< \default Float
    PulseWidth, ///< \brief Laser received pulse width (digitizer samples)
                ///< \default Float
    Deviation, ///< \brief Difference between the shape of the reference pulse and the return pulse. A larger value for deviation indicates larger distortion.
               ///< \default Float
    PassiveSignal, ///< \brief Relative passive signal
                   ///< \default Signed32
    BackgroundRadiation, ///< \brief A measure of background radiation.
                         ///< \default Float
    PassiveX, ///< \brief Passive X footprint
              ///< \default Double
    PassiveY, ///< \brief Passive Y footprint
              ///< \default Double
    PassiveZ, ///< \brief Passive Z footprint
              ///< \default Double
    XVelocity, ///< \brief X Velocity
               ///< \default Double
    YVelocity, ///< \brief Y Velocity
               ///< \default Double
    ZVelocity, ///< \brief Z Velocity
               ///< \default Double
    Azimuth, ///< \brief Scanner azimuth
             ///< \default Double
    WanderAngle, ///< \brief Wander Angle
                 ///< \default Double
    XBodyAccel, ///< \brief X Body Acceleration
                ///< \default Double
    YBodyAccel, ///< \brief Y Body Acceleration
                ///< \default Double
    ZBodyAccel, ///< \brief Z Body Acceleration
                ///< \default Double
    XBodyAngRate, ///< \brief X Body Angle Rate
                  ///< \default Double
    YBodyAngRate, ///< \brief Y Body Angle Rate
                  ///< \default Double
    ZBodyAngRate, ///< \brief Z Body Angle Rate
                  ///< \default Double
    Flag, ///< \brief Flag
          ///< \default Unsigned8
    Mark, ///< \brief Mark
          ///< \default Unsigned8
    Alpha, ///< \brief Alpha
           ///< \default Unsigned16
    EchoRange, ///< \brief Echo Range
               ///< \default Double
    ScanChannel, ///< \brief Scan Channel
                 ///< \default Unsigned8
    Infrared, ///< \brief Infrared
              ///< \default Unsigned16
    HeightAboveGround, ///< \brief Height Above Ground
                       ///< \default Double
    ClassFlags, ///< \brief Class Flags - deprecated in favor of Synthetic/KeyPoint/Withheld/Overlap
                ///< \default Unsigned8
    Synthetic, ///< \brief ASPRS Synthetic flag.  See LAS specification for details.
               ///< \default Unsigned8
    KeyPoint, ///< \brief ASPRS Key-Point flag.  See LAS specification for details.
              ///< \default Unsigned8
    Withheld, ///< \brief ASPRS Withheld flag.  See LAS specification for details.
              ///< \default Unsigned8
    Overlap, ///< \brief ASPRS Overlap flag.  See LAS specification for details.
             ///< \default Unsigned8
    LvisLfid, ///< \brief LVIS_LFID
              ///< \default Unsigned64
    ShotNumber, ///< \brief Shot Number
                ///< \default Unsigned64
    LongitudeCentroid, ///< \brief Longitude Centroid
                       ///< \default Double
    LatitudeCentroid, ///< \brief Latitude Centroid
                      ///< \default Double
    ElevationCentroid, ///< \brief Elevation Centroid
                       ///< \default Double
    LongitudeLow, ///< \brief Longitude Low
                  ///< \default Double
    LatitudeLow, ///< \brief Latitude Low
                 ///< \default Double
    ElevationLow, ///< \brief Elevation Low
                  ///< \default Double
    LongitudeHigh, ///< \brief Longitude High
                   ///< \default Double
    LatitudeHigh, ///< \brief Latitude High
                  ///< \default Double
    ElevationHigh, ///< \brief Elevation High
                   ///< \default Double
    PointId, ///< \brief An explicit representation of point ordering within a file, which allows this usually-implicit information to be preserved when reordering points.
             ///< \default Unsigned32
    OriginId, ///< \brief A file source ID from which the point originated.  This ID is global to a derivative dataset which may be aggregated from multiple files.
              ///< \default Unsigned32
    NormalX, ///< \brief X component of a vector normal to surface at this point
             ///< \default Double
    NormalY, ///< \brief Y component of a vector normal to surface at this point
             ///< \default Double
    NormalZ, ///< \brief Z component of a vector normal to surface at this point
             ///< \default Double
    Curvature, ///< \brief Curvature of surface at this point
               ///< \default Double
    Density, ///< \brief Estimate of point density
             ///< \default Double
    Omit, ///< \brief Used to shallowly mark a point as being omitted without removing it
          ///< \default Unsigned8
    ClusterID, ///< \brief ID assigned to a point by a point-clustering algorithm.
               ///< \default Signed64
    NNDistance, ///< \brief Distance metric related to a point's nearest neighbors.
                ///< \default Double
    TextureU, ///< \brief U component of a texture location at this point
              ///< \default Double
    TextureV, ///< \brief V component of a texture location at this point
              ///< \default Double
    TextureW, ///< \brief W component of a texture location at this point
              ///< \default Double
    Linearity, ///< \brief Linearity of a point; larger values indicate more linear regions.
               ///< \default Double
    Planarity, ///< \brief Planarity of a point; larger values indicate more planar regions.
               ///< \default Double
    Scattering, ///< \brief Scattering of a point; larger values incidate complex (scattered) 3D regions.
                ///< \default Double
    Verticality, ///< \brief Verticality of a point; larger values indicate vertical structure.
                 ///< \default Double
    Omnivariance, ///< \brief Omnivariance of a point; cube root of the product of all eigenvalues.
                  ///< \default Double
    Anisotropy, ///< \brief Anisotropy of a point; larger values indicate strong variance in multiple dimensions.
                ///< \default Double
    Eigenentropy, ///< \brief Eigenentropy of a point; small values indicate more ordered regions, while large values indicate disorder.
                  ///< \default Double
    EigenvalueSum, ///< \brief Sum of computed eigenvalues.
                   ///< \default Double
    SurfaceVariation, ///< \brief Surface variation of a point; larger values indicate higher surface variation.
                      ///< \default Double
    DemantkeVerticality, ///< \brief Verticality of a point; larger values indicate vertical structure (Demantke's variation).
                         ///< \default Double
    OptimalKNN, ///< \brief Optimal number of k nearest neighbors, such that eigenentropy is minimized.
                ///< \default Unsigned64
    OptimalRadius, ///< \brief Radius corresponding to optimal k nearest neighbors, such that eigenentropy is minimized.
                   ///< \default Double
    Coplanar, ///< \brief Indicator of whether or not a point is part of a coplanar neighborhood.
              ///< \default Unsigned8
    LocalReachabilityDistance, ///< \brief Reachability metric based on the NNDistance of a point's nearest neighbors.
                               ///< \default Double
    LocalOutlierFactor, ///< \brief Outlier factor based on the LocalReachabilityDistance of a point's nearest neighbors.
                        ///< \default Double
    Miniball, ///< \brief Metric capturing distance from a point to the center of the smallest enclosing ball encapsulating k-nearest neighbors, scaled by radius of the ball.
              ///< \default Double
    Reciprocity, ///< \brief Metric capturing percentage of k-nearest neighbors that also contain the current point in their k-neighborhood.
                 ///< \default Double
    Rank, ///< \brief Estimated rank of neighborhood of points.
          ///< \default Unsigned8
    Eigenvalue0, ///< \brief Smallest eigenvalue obtained form covariance of XYZ coordinates in k-neighborhood.
                 ///< \default Double
    Eigenvalue1, ///< \brief Middle eigenvalue obtained form covariance of XYZ coordinates in k-neighborhood.
                 ///< \default Double
    Eigenvalue2, ///< \brief Largest eigenvalue obtained form covariance of XYZ coordinates in k-neighborhood.
                 ///< \default Double
    PlaneFit, ///< \brief Metric capturing current point's point to plane distance compared to those in the same k-neighborhood used to estimate the plane.
              ///< \default Double
    RadialDensity, ///< \brief Estimate of radial point density
                   ///< \default Double
    BeamOriginX, ///< \brief Beam origin X coordinate of current laser shot
                 ///< \default Double
    BeamOriginY, ///< \brief Beam origin Y coordinate of current laser shot
                 ///< \default Double
    BeamOriginZ, ///< \brief Beam origin Z coordinate of current laser shot
                 ///< \default Double
    BeamDirectionX, ///< \brief Beam direction unit vector X coordinate
                    ///< \default Double
    BeamDirectionY, ///< \brief Beam direction unit vector Y coordinate
                    ///< \default Double
    BeamDirectionZ, ///< \brief Beam direction unit vector Z coordinate
                    ///< \default Double
    NorthPositionRMS, ///< \brief North position RMS error
                      ///< \default Double
    EastPositionRMS, ///< \brief East position RMS error
                     ///< \default Double
    DownPositionRMS, ///< \brief Down position RMS error
                     ///< \default Double
    NorthVelocityRMS, ///< \brief North velocity RMS error
                      ///< \default Double
    EastVelocityRMS, ///< \brief East velocity RMS error
                     ///< \default Double
    DownVelocityRMS, ///< \brief Down velocity RMS error
                     ///< \default Double
    RollRMS, ///< \brief Roll RMS error
             ///< \default Double
    PitchRMS, ///< \brief Pitch RMS error
              ///< \default Double
    HeadingRMS, ///< \brief Heading RMS error
                ///< \default Double
    Reliability, ///< \brief Reliability (or confidence) of points
                 ///< \default Double
    EchoPos, ///< \brief Difference in position of a peak of a return signal compared to a typical return from a hard surface
             ///< \default Double
    EchoNorm, ///< \brief Difference in shape of a return signal compared to a typical return from a hard surface.
              ///< \default Double
    ImgNbr, ///< \brief Stores the number of the image from which it gets the color.
            ///< \default Unsigned8
    Image, ///< \brief Index of the image use for texturation.
           ///< \default Unsigned16
    Dimension, ///< \brief Dimension of the points. three types of dimensions are available (Linear : points form a linear feature. Planar : points form a planar surface. Complex : random group of points).
               ///< \default Unsigned8
    SphericalRange, ///< \brief Range in context of spherical coordinates.
                    ///< \default Double
    SphericalAzimuth, ///< \brief Azimuth in context of spherical coordinates.
                      ///< \default Double
    SphericalElevation, ///< \brief Elevation in context of spherical coordinates.
                        ///< \default Double
    H3, ///< \brief H3 index id
        ///< \default Unsigned64
    Duplicate, ///< \brief Indicator of whether or not a point is a labeled duplicate
               ///< \default Unsigned8
    ShotTimestamp, ///< \brief Precise timestamp of laser shot in seconds (GpsTime or InternalTime depending on sync_to_pps).
                   ///< \default Double
    UnambiguousRange ///< \brief MTA zone width (unambiguous range) in meters. Required for zone correction calculation.
                     ///< \default Double
};
typedef std::vector<Id> IdList;


/// Get a description of a predefined dimension.
/// \param[in] id  Dimension ID.
/// \return  Dimension description.
inline std::string description(Id id)
{
    switch (id)
    {
    case Id::X:
        return "X coordinate";
    case Id::Y:
        return "Y coordinate";
    case Id::Z:
        return "Z coordinate";
    case Id::W:
        return "W coordinate";
    case Id::Intensity:
        return "Representation of the pulse return magnitude";
    case Id::Amplitude:
        return "This is the ratio of the received power to the power "
            "received at the detection limit expressed in dB";
    case Id::Reflectance:
        return "Ratio of the received power to the power that would be "
            "received from a white diffuse target at the same distance "
            "expressed in dB. The reflectance represents a range "
            "independent property of the target.  The surface normal of "
            "this target is assumed to be in parallel to the laser beam "
            "direction.";
    case Id::ReturnNumber:
        return "Pulse return number for a given output pulse. A given "
            "output laser pulse can have many returns, and they must be "
            "marked in order, starting with 1";
    case Id::NumberOfReturns:
        return "Total number of returns for a given pulse.";
    case Id::ScanDirectionFlag:
        return "Direction at which the scanner mirror was traveling at the "
            "time of the output pulse. A value of 1 is a positive scan "
            "direction, and a bit value of 0 is a negative scan direction, "
            "where positive scan direction is a scan moving from the left "
            "side of the in-track direction to the right side and negative "
            "the opposite";
    case Id::EdgeOfFlightLine:
        return "Indicates the end of scanline before a direction change "
            "with a value of 1 - 0 otherwise";
    case Id::Classification:
        return "ASPRS classification.  0 for no classification.  See LAS "
            "specification for details.";
    case Id::ScanAngleRank:
        return "Angle degree at which the laser point was output from the "
            "system, including the roll of the aircraft.  The scan angle is "
            "based on being nadir, and -90 the left side of the aircraft in "
            "the direction of flight";
    case Id::UserData:
        return "Unspecified user data";
    case Id::PointSourceId:
        return "File source ID from which the point originated.  Zero "
            "indicates that the point originated in the current file";
    case Id::Red:
        return "Red image channel value";
    case Id::Green:
        return "Green image channel value";
    case Id::Blue:
        return "Blue image channel value";
    case Id::GpsTime:
        return "GPS time that the point was acquired";
    case Id::InternalTime:
        return "Scanner's internal time when the point was acquired, in "
            "seconds";
    case Id::OffsetTime:
        return "Milliseconds from first acquired point";
    case Id::IsPpsLocked:
        return "The external PPS signal was found to be synchronized at the "
            "time of the current laser shot.";
    case Id::StartPulse:
        return "Relative pulse signal strength";
    case Id::ReflectedPulse:
        return "Relative reflected pulse signal strength";
    case Id::Pdop:
        return "GPS PDOP (dilution of precision)";
    case Id::Pitch:
        return "Pitch in degrees";
    case Id::Roll:
        return "Roll in degrees";
    case Id::PulseWidth:
        return "Laser received pulse width (digitizer samples)";
    case Id::Deviation:
        return "Difference between the shape of the reference pulse and the "
            "return pulse. A larger value for deviation indicates larger "
            "distortion.";
    case Id::PassiveSignal:
        return "Relative passive signal";
    case Id::BackgroundRadiation:
        return "A measure of background radiation.";
    case Id::PassiveX:
        return "Passive X footprint";
    case Id::PassiveY:
        return "Passive Y footprint";
    case Id::PassiveZ:
        return "Passive Z footprint";
    case Id::XVelocity:
        return "X Velocity";
    case Id::YVelocity:
        return "Y Velocity";
    case Id::ZVelocity:
        return "Z Velocity";
    case Id::Azimuth:
        return "Scanner azimuth";
    case Id::WanderAngle:
        return "Wander Angle";
    case Id::XBodyAccel:
        return "X Body Acceleration";
    case Id::YBodyAccel:
        return "Y Body Acceleration";
    case Id::ZBodyAccel:
        return "Z Body Acceleration";
    case Id::XBodyAngRate:
        return "X Body Angle Rate";
    case Id::YBodyAngRate:
        return "Y Body Angle Rate";
    case Id::ZBodyAngRate:
        return "Z Body Angle Rate";
    case Id::Flag:
        return "Flag";
    case Id::Mark:
        return "Mark";
    case Id::Alpha:
        return "Alpha";
    case Id::EchoRange:
        return "Echo Range";
    case Id::ScanChannel:
        return "Scan Channel";
    case Id::Infrared:
        return "Infrared";
    case Id::HeightAboveGround:
        return "Height Above Ground";
    case Id::ClassFlags:
        return "Class Flags - deprecated in favor of "
            "Synthetic/KeyPoint/Withheld/Overlap";
    case Id::Synthetic:
        return "ASPRS Synthetic flag.  See LAS specification for details.";
    case Id::KeyPoint:
        return "ASPRS Key-Point flag.  See LAS specification for details.";
    case Id::Withheld:
        return "ASPRS Withheld flag.  See LAS specification for details.";
    case Id::Overlap:
        return "ASPRS Overlap flag.  See LAS specification for details.";
    case Id::LvisLfid:
        return "LVIS_LFID";
    case Id::ShotNumber:
        return "Shot Number";
    case Id::LongitudeCentroid:
        return "Longitude Centroid";
    case Id::LatitudeCentroid:
        return "Latitude Centroid";
    case Id::ElevationCentroid:
        return "Elevation Centroid";
    case Id::LongitudeLow:
        return "Longitude Low";
    case Id::LatitudeLow:
        return "Latitude Low";
    case Id::ElevationLow:
        return "Elevation Low";
    case Id::LongitudeHigh:
        return "Longitude High";
    case Id::LatitudeHigh:
        return "Latitude High";
    case Id::ElevationHigh:
        return "Elevation High";
    case Id::PointId:
        return "An explicit representation of point ordering within a file, "
            "which allows this usually-implicit information to be preserved "
            "when reordering points.";
    case Id::OriginId:
        return "A file source ID from which the point originated.  This ID "
            "is global to a derivative dataset which may be aggregated from "
            "multiple files.";
    case Id::NormalX:
        return "X component of a vector normal to surface at this point";
    case Id::NormalY:
        return "Y component of a vector normal to surface at this point";
    case Id::NormalZ:
        return "Z component of a vector normal to surface at this point";
    case Id::Curvature:
        return "Curvature of surface at this point";
    case Id::Density:
        return "Estimate of point density";
    case Id::Omit:
        return "Used to shallowly mark a point as being omitted without "
            "removing it";
    case Id::ClusterID:
        return "ID assigned to a point by a point-clustering algorithm.";
    case Id::NNDistance:
        return "Distance metric related to a point's nearest neighbors.";
    case Id::TextureU:
        return "U component of a texture location at this point";
    case Id::TextureV:
        return "V component of a texture location at this point";
    case Id::TextureW:
        return "W component of a texture location at this point";
    case Id::Linearity:
        return "Linearity of a point; larger values indicate more linear "
            "regions.";
    case Id::Planarity:
        return "Planarity of a point; larger values indicate more planar "
            "regions.";
    case Id::Scattering:
        return "Scattering of a point; larger values incidate complex "
            "(scattered) 3D regions.";
    case Id::Verticality:
        return "Verticality of a point; larger values indicate vertical "
            "structure.";
    case Id::Omnivariance:
        return "Omnivariance of a point; cube root of the product of all "
            "eigenvalues.";
    case Id::Anisotropy:
        return "Anisotropy of a point; larger values indicate strong "
            "variance in multiple dimensions.";
    case Id::Eigenentropy:
        return "Eigenentropy of a point; small values indicate more ordered "
            "regions, while large values indicate disorder.";
    case Id::EigenvalueSum:
        return "Sum of computed eigenvalues.";
    case Id::SurfaceVariation:
        return "Surface variation of a point; larger values indicate higher "
            "surface variation.";
    case Id::DemantkeVerticality:
        return "Verticality of a point; larger values indicate vertical "
            "structure (Demantke's variation).";
    case Id::OptimalKNN:
        return "Optimal number of k nearest neighbors, such that "
            "eigenentropy is minimized.";
    case Id::OptimalRadius:
        return "Radius corresponding to optimal k nearest neighbors, such "
            "that eigenentropy is minimized.";
    case Id::Coplanar:
        return "Indicator of whether or not a point is part of a coplanar "
            "neighborhood.";
    case Id::LocalReachabilityDistance:
        return "Reachability metric based on the NNDistance of a point's "
            "nearest neighbors.";
    case Id::LocalOutlierFactor:
        return "Outlier factor based on the LocalReachabilityDistance of a "
            "point's nearest neighbors.";
    case Id::Miniball:
        return "Metric capturing distance from a point to the center of the "
            "smallest enclosing ball encapsulating k-nearest neighbors, "
            "scaled by radius of the ball.";
    case Id::Reciprocity:
        return "Metric capturing percentage of k-nearest neighbors that "
            "also contain the current point in their k-neighborhood.";
    case Id::Rank:
        return "Estimated rank of neighborhood of points.";
    case Id::Eigenvalue0:
        return "Smallest eigenvalue obtained form covariance of XYZ "
            "coordinates in k-neighborhood.";
    case Id::Eigenvalue1:
        return "Middle eigenvalue obtained form covariance of XYZ "
            "coordinates in k-neighborhood.";
    case Id::Eigenvalue2:
        return "Largest eigenvalue obtained form covariance of XYZ "
            "coordinates in k-neighborhood.";
    case Id::PlaneFit:
        return "Metric capturing current point's point to plane distance "
            "compared to those in the same k-neighborhood used to estimate "
            "the plane.";
    case Id::RadialDensity:
        return "Estimate of radial point density";
    case Id::BeamOriginX:
        return "Beam origin X coordinate of current laser shot";
    case Id::BeamOriginY:
        return "Beam origin Y coordinate of current laser shot";
    case Id::BeamOriginZ:
        return "Beam origin Z coordinate of current laser shot";
    case Id::BeamDirectionX:
        return "Beam direction unit vector X coordinate";
    case Id::BeamDirectionY:
        return "Beam direction unit vector Y coordinate";
    case Id::BeamDirectionZ:
        return "Beam direction unit vector Z coordinate";
    case Id::NorthPositionRMS:
        return "North position RMS error";
    case Id::EastPositionRMS:
        return "East position RMS error";
    case Id::DownPositionRMS:
        return "Down position RMS error";
    case Id::NorthVelocityRMS:
        return "North velocity RMS error";
    case Id::EastVelocityRMS:
        return "East velocity RMS error";
    case Id::DownVelocityRMS:
        return "Down velocity RMS error";
    case Id::RollRMS:
        return "Roll RMS error";
    case Id::PitchRMS:
        return "Pitch RMS error";
    case Id::HeadingRMS:
        return "Heading RMS error";
    case Id::Reliability:
        return "Reliability (or confidence) of points";
    case Id::EchoPos:
        return "Difference in position of a peak of a return signal "
            "compared to a typical return from a hard surface";
    case Id::EchoNorm:
        return "Difference in shape of a return signal compared to a "
            "typical return from a hard surface.";
    case Id::ImgNbr:
        return "Stores the number of the image from which it gets the color.";
    case Id::Image:
        return "Index of the image use for texturation.";
    case Id::Dimension:
        return "Dimension of the points. three types of dimensions are "
            "available (Linear : points form a linear feature. Planar : "
            "points form a planar surface. Complex : random group of "
            "points).";
    case Id::SphericalRange:
        return "Range in context of spherical coordinates.";
    case Id::SphericalAzimuth:
        return "Azimuth in context of spherical coordinates.";
    case Id::SphericalElevation:
        return "Elevation in context of spherical coordinates.";
    case Id::H3:
        return "H3 index id";
    case Id::Duplicate:
        return "Indicator of whether or not a point is a labeled duplicate";
    case Id::ShotTimestamp:
        return "Precise timestamp of laser shot in seconds (GpsTime or "
            "InternalTime depending on sync_to_pps).";
    case Id::UnambiguousRange:
        return "MTA zone width (unambiguous range) in meters. Required for "
            "zone correction calculation.";
    case Id::Unknown:
        return "";
    }
    return "";
}

/// Get a predefined dimension ID given a dimension name. Multiple names
/// may map to the same dimension for convenience.  Names are case-insensitive.
/// \param[in] s  Name of dimension.
/// \return  Dimension ID associated with the name.  Id::Unknown is returned
///    if the name doesn't map to a predefined dimension.
inline Id id(std::string s)
{
    s = Utils::toupper(s);

    if (s == "X")
        return Id::X;
    if (s == "Y")
        return Id::Y;
    if (s == "Z")
        return Id::Z;
    if (s == "W")
        return Id::W;
    if (s == "INTENSITY")
        return Id::Intensity;
    if (s == "AMPLITUDE")
        return Id::Amplitude;
    if (s == "REFLECTANCE")
        return Id::Reflectance;
    if (s == "RETURNNUMBER")
        return Id::ReturnNumber;
    if (s == "NUMBEROFRETURNS")
        return Id::NumberOfReturns;
    if (s == "SCANDIRECTIONFLAG")
        return Id::ScanDirectionFlag;
    if (s == "EDGEOFFLIGHTLINE")
        return Id::EdgeOfFlightLine;
    if (s == "CLASSIFICATION")
        return Id::Classification;
    if (s == "CLASS")
        return Id::Classification;
    if (s == "CLS")
        return Id::Classification;
    if (s == "SCANANGLERANK")
        return Id::ScanAngleRank;
    if (s == "SCANANGLE")
        return Id::ScanAngleRank;
    if (s == "USERDATA")
        return Id::UserData;
    if (s == "USER_DATA")
        return Id::UserData;
    if (s == "POINTSOURCEID")
        return Id::PointSourceId;
    if (s == "RED")
        return Id::Red;
    if (s == "DIFFUSE_RED")
        return Id::Red;
    if (s == "GREEN")
        return Id::Green;
    if (s == "DIFFUSE_GREEN")
        return Id::Green;
    if (s == "BLUE")
        return Id::Blue;
    if (s == "DIFFUSE_BLUE")
        return Id::Blue;
    if (s == "GPSTIME")
        return Id::GpsTime;
    if (s == "INTERNALTIME")
        return Id::InternalTime;
    if (s == "OFFSETTIME")
        return Id::OffsetTime;
    if (s == "TIME")
        return Id::OffsetTime;
    if (s == "ISPPSLOCKED")
        return Id::IsPpsLocked;
    if (s == "STARTPULSE")
        return Id::StartPulse;
    if (s == "REFLECTEDPULSE")
        return Id::ReflectedPulse;
    if (s == "PDOP")
        return Id::Pdop;
    if (s == "PITCH")
        return Id::Pitch;
    if (s == "ROLL")
        return Id::Roll;
    if (s == "PULSEWIDTH")
        return Id::PulseWidth;
    if (s == "DEVIATION")
        return Id::Deviation;
    if (s == "PASSIVESIGNAL")
        return Id::PassiveSignal;
    if (s == "BACKGROUNDRADIATION")
        return Id::BackgroundRadiation;
    if (s == "PASSIVEX")
        return Id::PassiveX;
    if (s == "PASSIVEY")
        return Id::PassiveY;
    if (s == "PASSIVEZ")
        return Id::PassiveZ;
    if (s == "XVELOCITY")
        return Id::XVelocity;
    if (s == "YVELOCITY")
        return Id::YVelocity;
    if (s == "ZVELOCITY")
        return Id::ZVelocity;
    if (s == "AZIMUTH")
        return Id::Azimuth;
    if (s == "PLATFORMHEADING")
        return Id::Azimuth;
    if (s == "WANDERANGLE")
        return Id::WanderAngle;
    if (s == "XBODYACCEL")
        return Id::XBodyAccel;
    if (s == "YBODYACCEL")
        return Id::YBodyAccel;
    if (s == "ZBODYACCEL")
        return Id::ZBodyAccel;
    if (s == "XBODYANGRATE")
        return Id::XBodyAngRate;
    if (s == "YBODYANGRATE")
        return Id::YBodyAngRate;
    if (s == "ZBODYANGRATE")
        return Id::ZBodyAngRate;
    if (s == "FLAG")
        return Id::Flag;
    if (s == "MARK")
        return Id::Mark;
    if (s == "ALPHA")
        return Id::Alpha;
    if (s == "ECHORANGE")
        return Id::EchoRange;
    if (s == "SCANCHANNEL")
        return Id::ScanChannel;
    if (s == "INFRARED")
        return Id::Infrared;
    if (s == "NEARINFRARED")
        return Id::Infrared;
    if (s == "HEIGHTABOVEGROUND")
        return Id::HeightAboveGround;
    if (s == "HEIGHT ABOVE GROUND")
        return Id::HeightAboveGround;
    if (s == "HAG")
        return Id::HeightAboveGround;
    if (s == "CLASSFLAGS")
        return Id::ClassFlags;
    if (s == "SYNTHETIC")
        return Id::Synthetic;
    if (s == "KEYPOINT")
        return Id::KeyPoint;
    if (s == "WITHHELD")
        return Id::Withheld;
    if (s == "OVERLAP")
        return Id::Overlap;
    if (s == "LVISLFID")
        return Id::LvisLfid;
    if (s == "LVIS_LFID")
        return Id::LvisLfid;
    if (s == "SHOTNUMBER")
        return Id::ShotNumber;
    if (s == "LONGITUDECENTROID")
        return Id::LongitudeCentroid;
    if (s == "LONGITUDE_CENTROID")
        return Id::LongitudeCentroid;
    if (s == "LATITUDECENTROID")
        return Id::LatitudeCentroid;
    if (s == "LATITUDE_CENTROID")
        return Id::LatitudeCentroid;
    if (s == "ELEVATIONCENTROID")
        return Id::ElevationCentroid;
    if (s == "ELEVATION_CENTROID")
        return Id::ElevationCentroid;
    if (s == "LONGITUDELOW")
        return Id::LongitudeLow;
    if (s == "LONGITUDE_LOW")
        return Id::LongitudeLow;
    if (s == "LATITUDELOW")
        return Id::LatitudeLow;
    if (s == "LATITUDE_LOW")
        return Id::LatitudeLow;
    if (s == "ELEVATIONLOW")
        return Id::ElevationLow;
    if (s == "ELEVATION_LOW")
        return Id::ElevationLow;
    if (s == "LONGITUDEHIGH")
        return Id::LongitudeHigh;
    if (s == "LONGITUDE_HIGH")
        return Id::LongitudeHigh;
    if (s == "LATITUDEHIGH")
        return Id::LatitudeHigh;
    if (s == "LATITUDE_HIGH")
        return Id::LatitudeHigh;
    if (s == "ELEVATIONHIGH")
        return Id::ElevationHigh;
    if (s == "ELEVATION_HIGH")
        return Id::ElevationHigh;
    if (s == "POINTID")
        return Id::PointId;
    if (s == "ORIGINID")
        return Id::OriginId;
    if (s == "NORMALX")
        return Id::NormalX;
    if (s == "NX")
        return Id::NormalX;
    if (s == "NORMAL_X")
        return Id::NormalX;
    if (s == "NORMALX")
        return Id::NormalX;
    if (s == "NORMAL X")
        return Id::NormalX;
    if (s == "NORMALY")
        return Id::NormalY;
    if (s == "NY")
        return Id::NormalY;
    if (s == "NORMAL_Y")
        return Id::NormalY;
    if (s == "NORMALY")
        return Id::NormalY;
    if (s == "NORMAL Y")
        return Id::NormalY;
    if (s == "NORMALZ")
        return Id::NormalZ;
    if (s == "NZ")
        return Id::NormalZ;
    if (s == "NORMAL_Z")
        return Id::NormalZ;
    if (s == "NORMALZ")
        return Id::NormalZ;
    if (s == "NORMAL Z")
        return Id::NormalZ;
    if (s == "CURVATURE")
        return Id::Curvature;
    if (s == "DENSITY")
        return Id::Density;
    if (s == "OMIT")
        return Id::Omit;
    if (s == "CLUSTERID")
        return Id::ClusterID;
    if (s == "NNDISTANCE")
        return Id::NNDistance;
    if (s == "TEXTUREU")
        return Id::TextureU;
    if (s == "TU")
        return Id::TextureU;
    if (s == "TEXTURE_U")
        return Id::TextureU;
    if (s == "TEXTUREU")
        return Id::TextureU;
    if (s == "TEXTUREV")
        return Id::TextureV;
    if (s == "TV")
        return Id::TextureV;
    if (s == "TEXTURE_V")
        return Id::TextureV;
    if (s == "TEXTUREV")
        return Id::TextureV;
    if (s == "TEXTUREW")
        return Id::TextureW;
    if (s == "TW")
        return Id::TextureW;
    if (s == "TEXTURE_W")
        return Id::TextureW;
    if (s == "TEXTUREW")
        return Id::TextureW;
    if (s == "LINEARITY")
        return Id::Linearity;
    if (s == "PLANARITY")
        return Id::Planarity;
    if (s == "SCATTERING")
        return Id::Scattering;
    if (s == "VERTICALITY")
        return Id::Verticality;
    if (s == "OMNIVARIANCE")
        return Id::Omnivariance;
    if (s == "ANISOTROPY")
        return Id::Anisotropy;
    if (s == "EIGENENTROPY")
        return Id::Eigenentropy;
    if (s == "EIGENVALUESUM")
        return Id::EigenvalueSum;
    if (s == "SURFACEVARIATION")
        return Id::SurfaceVariation;
    if (s == "DEMANTKEVERTICALITY")
        return Id::DemantkeVerticality;
    if (s == "OPTIMALKNN")
        return Id::OptimalKNN;
    if (s == "OPTIMALRADIUS")
        return Id::OptimalRadius;
    if (s == "COPLANAR")
        return Id::Coplanar;
    if (s == "LOCALREACHABILITYDISTANCE")
        return Id::LocalReachabilityDistance;
    if (s == "LOCALOUTLIERFACTOR")
        return Id::LocalOutlierFactor;
    if (s == "MINIBALL")
        return Id::Miniball;
    if (s == "RECIPROCITY")
        return Id::Reciprocity;
    if (s == "RANK")
        return Id::Rank;
    if (s == "EIGENVALUE0")
        return Id::Eigenvalue0;
    if (s == "EIGENVALUE1")
        return Id::Eigenvalue1;
    if (s == "EIGENVALUE2")
        return Id::Eigenvalue2;
    if (s == "PLANEFIT")
        return Id::PlaneFit;
    if (s == "RADIALDENSITY")
        return Id::RadialDensity;
    if (s == "BEAMORIGINX")
        return Id::BeamOriginX;
    if (s == "BEAMORIGINY")
        return Id::BeamOriginY;
    if (s == "BEAMORIGINZ")
        return Id::BeamOriginZ;
    if (s == "BEAMDIRECTIONX")
        return Id::BeamDirectionX;
    if (s == "BEAMDIRECTIONY")
        return Id::BeamDirectionY;
    if (s == "BEAMDIRECTIONZ")
        return Id::BeamDirectionZ;
    if (s == "NORTHPOSITIONRMS")
        return Id::NorthPositionRMS;
    if (s == "EASTPOSITIONRMS")
        return Id::EastPositionRMS;
    if (s == "DOWNPOSITIONRMS")
        return Id::DownPositionRMS;
    if (s == "NORTHVELOCITYRMS")
        return Id::NorthVelocityRMS;
    if (s == "EASTVELOCITYRMS")
        return Id::EastVelocityRMS;
    if (s == "DOWNVELOCITYRMS")
        return Id::DownVelocityRMS;
    if (s == "ROLLRMS")
        return Id::RollRMS;
    if (s == "PITCHRMS")
        return Id::PitchRMS;
    if (s == "HEADINGRMS")
        return Id::HeadingRMS;
    if (s == "RELIABILITY")
        return Id::Reliability;
    if (s == "ECHOPOS")
        return Id::EchoPos;
    if (s == "ECHONORM")
        return Id::EchoNorm;
    if (s == "IMGNBR")
        return Id::ImgNbr;
    if (s == "IMAGE")
        return Id::Image;
    if (s == "DIMENSION")
        return Id::Dimension;
    if (s == "SPHERICALRANGE")
        return Id::SphericalRange;
    if (s == "SPHERICALAZIMUTH")
        return Id::SphericalAzimuth;
    if (s == "SPHERICALELEVATION")
        return Id::SphericalElevation;
    if (s == "H3")
        return Id::H3;
    if (s == "DUPLICATE")
        return Id::Duplicate;
    if (s == "SHOTTIMESTAMP")
        return Id::ShotTimestamp;
    if (s == "UNAMBIGUOUSRANGE")
        return Id::UnambiguousRange;
    return Id::Unknown;
}

/// Get the name of a predefined dimension.
/// \param[in] id  Dimension ID
/// \return  Dimension name.
inline std::string name(Id id)
{
    switch (id)
    {
    case Id::X:
        return "X";
    case Id::Y:
        return "Y";
    case Id::Z:
        return "Z";
    case Id::W:
        return "W";
    case Id::Intensity:
        return "Intensity";
    case Id::Amplitude:
        return "Amplitude";
    case Id::Reflectance:
        return "Reflectance";
    case Id::ReturnNumber:
        return "ReturnNumber";
    case Id::NumberOfReturns:
        return "NumberOfReturns";
    case Id::ScanDirectionFlag:
        return "ScanDirectionFlag";
    case Id::EdgeOfFlightLine:
        return "EdgeOfFlightLine";
    case Id::Classification:
        return "Classification";
    case Id::ScanAngleRank:
        return "ScanAngleRank";
    case Id::UserData:
        return "UserData";
    case Id::PointSourceId:
        return "PointSourceId";
    case Id::Red:
        return "Red";
    case Id::Green:
        return "Green";
    case Id::Blue:
        return "Blue";
    case Id::GpsTime:
        return "GpsTime";
    case Id::InternalTime:
        return "InternalTime";
    case Id::OffsetTime:
        return "OffsetTime";
    case Id::IsPpsLocked:
        return "IsPpsLocked";
    case Id::StartPulse:
        return "StartPulse";
    case Id::ReflectedPulse:
        return "ReflectedPulse";
    case Id::Pdop:
        return "Pdop";
    case Id::Pitch:
        return "Pitch";
    case Id::Roll:
        return "Roll";
    case Id::PulseWidth:
        return "PulseWidth";
    case Id::Deviation:
        return "Deviation";
    case Id::PassiveSignal:
        return "PassiveSignal";
    case Id::BackgroundRadiation:
        return "BackgroundRadiation";
    case Id::PassiveX:
        return "PassiveX";
    case Id::PassiveY:
        return "PassiveY";
    case Id::PassiveZ:
        return "PassiveZ";
    case Id::XVelocity:
        return "XVelocity";
    case Id::YVelocity:
        return "YVelocity";
    case Id::ZVelocity:
        return "ZVelocity";
    case Id::Azimuth:
        return "Azimuth";
    case Id::WanderAngle:
        return "WanderAngle";
    case Id::XBodyAccel:
        return "XBodyAccel";
    case Id::YBodyAccel:
        return "YBodyAccel";
    case Id::ZBodyAccel:
        return "ZBodyAccel";
    case Id::XBodyAngRate:
        return "XBodyAngRate";
    case Id::YBodyAngRate:
        return "YBodyAngRate";
    case Id::ZBodyAngRate:
        return "ZBodyAngRate";
    case Id::Flag:
        return "Flag";
    case Id::Mark:
        return "Mark";
    case Id::Alpha:
        return "Alpha";
    case Id::EchoRange:
        return "EchoRange";
    case Id::ScanChannel:
        return "ScanChannel";
    case Id::Infrared:
        return "Infrared";
    case Id::HeightAboveGround:
        return "HeightAboveGround";
    case Id::ClassFlags:
        return "ClassFlags";
    case Id::Synthetic:
        return "Synthetic";
    case Id::KeyPoint:
        return "KeyPoint";
    case Id::Withheld:
        return "Withheld";
    case Id::Overlap:
        return "Overlap";
    case Id::LvisLfid:
        return "LvisLfid";
    case Id::ShotNumber:
        return "ShotNumber";
    case Id::LongitudeCentroid:
        return "LongitudeCentroid";
    case Id::LatitudeCentroid:
        return "LatitudeCentroid";
    case Id::ElevationCentroid:
        return "ElevationCentroid";
    case Id::LongitudeLow:
        return "LongitudeLow";
    case Id::LatitudeLow:
        return "LatitudeLow";
    case Id::ElevationLow:
        return "ElevationLow";
    case Id::LongitudeHigh:
        return "LongitudeHigh";
    case Id::LatitudeHigh:
        return "LatitudeHigh";
    case Id::ElevationHigh:
        return "ElevationHigh";
    case Id::PointId:
        return "PointId";
    case Id::OriginId:
        return "OriginId";
    case Id::NormalX:
        return "NormalX";
    case Id::NormalY:
        return "NormalY";
    case Id::NormalZ:
        return "NormalZ";
    case Id::Curvature:
        return "Curvature";
    case Id::Density:
        return "Density";
    case Id::Omit:
        return "Omit";
    case Id::ClusterID:
        return "ClusterID";
    case Id::NNDistance:
        return "NNDistance";
    case Id::TextureU:
        return "TextureU";
    case Id::TextureV:
        return "TextureV";
    case Id::TextureW:
        return "TextureW";
    case Id::Linearity:
        return "Linearity";
    case Id::Planarity:
        return "Planarity";
    case Id::Scattering:
        return "Scattering";
    case Id::Verticality:
        return "Verticality";
    case Id::Omnivariance:
        return "Omnivariance";
    case Id::Anisotropy:
        return "Anisotropy";
    case Id::Eigenentropy:
        return "Eigenentropy";
    case Id::EigenvalueSum:
        return "EigenvalueSum";
    case Id::SurfaceVariation:
        return "SurfaceVariation";
    case Id::DemantkeVerticality:
        return "DemantkeVerticality";
    case Id::OptimalKNN:
        return "OptimalKNN";
    case Id::OptimalRadius:
        return "OptimalRadius";
    case Id::Coplanar:
        return "Coplanar";
    case Id::LocalReachabilityDistance:
        return "LocalReachabilityDistance";
    case Id::LocalOutlierFactor:
        return "LocalOutlierFactor";
    case Id::Miniball:
        return "Miniball";
    case Id::Reciprocity:
        return "Reciprocity";
    case Id::Rank:
        return "Rank";
    case Id::Eigenvalue0:
        return "Eigenvalue0";
    case Id::Eigenvalue1:
        return "Eigenvalue1";
    case Id::Eigenvalue2:
        return "Eigenvalue2";
    case Id::PlaneFit:
        return "PlaneFit";
    case Id::RadialDensity:
        return "RadialDensity";
    case Id::BeamOriginX:
        return "BeamOriginX";
    case Id::BeamOriginY:
        return "BeamOriginY";
    case Id::BeamOriginZ:
        return "BeamOriginZ";
    case Id::BeamDirectionX:
        return "BeamDirectionX";
    case Id::BeamDirectionY:
        return "BeamDirectionY";
    case Id::BeamDirectionZ:
        return "BeamDirectionZ";
    case Id::NorthPositionRMS:
        return "NorthPositionRMS";
    case Id::EastPositionRMS:
        return "EastPositionRMS";
    case Id::DownPositionRMS:
        return "DownPositionRMS";
    case Id::NorthVelocityRMS:
        return "NorthVelocityRMS";
    case Id::EastVelocityRMS:
        return "EastVelocityRMS";
    case Id::DownVelocityRMS:
        return "DownVelocityRMS";
    case Id::RollRMS:
        return "RollRMS";
    case Id::PitchRMS:
        return "PitchRMS";
    case Id::HeadingRMS:
        return "HeadingRMS";
    case Id::Reliability:
        return "Reliability";
    case Id::EchoPos:
        return "EchoPos";
    case Id::EchoNorm:
        return "EchoNorm";
    case Id::ImgNbr:
        return "ImgNbr";
    case Id::Image:
        return "Image";
    case Id::Dimension:
        return "Dimension";
    case Id::SphericalRange:
        return "SphericalRange";
    case Id::SphericalAzimuth:
        return "SphericalAzimuth";
    case Id::SphericalElevation:
        return "SphericalElevation";
    case Id::H3:
        return "H3";
    case Id::Duplicate:
        return "Duplicate";
    case Id::ShotTimestamp:
        return "ShotTimestamp";
    case Id::UnambiguousRange:
        return "UnambiguousRange";
    case Id::Unknown:
        return "";
    }
    return "";
}

/// Get the default storage type of a predefined dimension.
/// \param id  ID of the predefined dimension.
/// \return  The dimension's default storage type.  An exception is thrown if
///   the id doesn't represent a predefined dimension.
inline Type defaultType(Id id)
{
    switch (id)
    {
    case Id::X:
        return Type::Double;
    case Id::Y:
        return Type::Double;
    case Id::Z:
        return Type::Double;
    case Id::W:
        return Type::Double;
    case Id::Intensity:
        return Type::Unsigned16;
    case Id::Amplitude:
        return Type::Float;
    case Id::Reflectance:
        return Type::Float;
    case Id::ReturnNumber:
        return Type::Unsigned8;
    case Id::NumberOfReturns:
        return Type::Unsigned8;
    case Id::ScanDirectionFlag:
        return Type::Unsigned8;
    case Id::EdgeOfFlightLine:
        return Type::Unsigned8;
    case Id::Classification:
        return Type::Unsigned8;
    case Id::ScanAngleRank:
        return Type::Float;
    case Id::UserData:
        return Type::Unsigned8;
    case Id::PointSourceId:
        return Type::Unsigned16;
    case Id::Red:
        return Type::Unsigned16;
    case Id::Green:
        return Type::Unsigned16;
    case Id::Blue:
        return Type::Unsigned16;
    case Id::GpsTime:
        return Type::Double;
    case Id::InternalTime:
        return Type::Double;
    case Id::OffsetTime:
        return Type::Unsigned32;
    case Id::IsPpsLocked:
        return Type::Unsigned8;
    case Id::StartPulse:
        return Type::Signed32;
    case Id::ReflectedPulse:
        return Type::Signed32;
    case Id::Pdop:
        return Type::Float;
    case Id::Pitch:
        return Type::Float;
    case Id::Roll:
        return Type::Float;
    case Id::PulseWidth:
        return Type::Float;
    case Id::Deviation:
        return Type::Float;
    case Id::PassiveSignal:
        return Type::Signed32;
    case Id::BackgroundRadiation:
        return Type::Float;
    case Id::PassiveX:
        return Type::Double;
    case Id::PassiveY:
        return Type::Double;
    case Id::PassiveZ:
        return Type::Double;
    case Id::XVelocity:
        return Type::Double;
    case Id::YVelocity:
        return Type::Double;
    case Id::ZVelocity:
        return Type::Double;
    case Id::Azimuth:
        return Type::Double;
    case Id::WanderAngle:
        return Type::Double;
    case Id::XBodyAccel:
        return Type::Double;
    case Id::YBodyAccel:
        return Type::Double;
    case Id::ZBodyAccel:
        return Type::Double;
    case Id::XBodyAngRate:
        return Type::Double;
    case Id::YBodyAngRate:
        return Type::Double;
    case Id::ZBodyAngRate:
        return Type::Double;
    case Id::Flag:
        return Type::Unsigned8;
    case Id::Mark:
        return Type::Unsigned8;
    case Id::Alpha:
        return Type::Unsigned16;
    case Id::EchoRange:
        return Type::Double;
    case Id::ScanChannel:
        return Type::Unsigned8;
    case Id::Infrared:
        return Type::Unsigned16;
    case Id::HeightAboveGround:
        return Type::Double;
    case Id::ClassFlags:
        return Type::Unsigned8;
    case Id::Synthetic:
        return Type::Unsigned8;
    case Id::KeyPoint:
        return Type::Unsigned8;
    case Id::Withheld:
        return Type::Unsigned8;
    case Id::Overlap:
        return Type::Unsigned8;
    case Id::LvisLfid:
        return Type::Unsigned64;
    case Id::ShotNumber:
        return Type::Unsigned64;
    case Id::LongitudeCentroid:
        return Type::Double;
    case Id::LatitudeCentroid:
        return Type::Double;
    case Id::ElevationCentroid:
        return Type::Double;
    case Id::LongitudeLow:
        return Type::Double;
    case Id::LatitudeLow:
        return Type::Double;
    case Id::ElevationLow:
        return Type::Double;
    case Id::LongitudeHigh:
        return Type::Double;
    case Id::LatitudeHigh:
        return Type::Double;
    case Id::ElevationHigh:
        return Type::Double;
    case Id::PointId:
        return Type::Unsigned32;
    case Id::OriginId:
        return Type::Unsigned32;
    case Id::NormalX:
        return Type::Double;
    case Id::NormalY:
        return Type::Double;
    case Id::NormalZ:
        return Type::Double;
    case Id::Curvature:
        return Type::Double;
    case Id::Density:
        return Type::Double;
    case Id::Omit:
        return Type::Unsigned8;
    case Id::ClusterID:
        return Type::Signed64;
    case Id::NNDistance:
        return Type::Double;
    case Id::TextureU:
        return Type::Double;
    case Id::TextureV:
        return Type::Double;
    case Id::TextureW:
        return Type::Double;
    case Id::Linearity:
        return Type::Double;
    case Id::Planarity:
        return Type::Double;
    case Id::Scattering:
        return Type::Double;
    case Id::Verticality:
        return Type::Double;
    case Id::Omnivariance:
        return Type::Double;
    case Id::Anisotropy:
        return Type::Double;
    case Id::Eigenentropy:
        return Type::Double;
    case Id::EigenvalueSum:
        return Type::Double;
    case Id::SurfaceVariation:
        return Type::Double;
    case Id::DemantkeVerticality:
        return Type::Double;
    case Id::OptimalKNN:
        return Type::Unsigned64;
    case Id::OptimalRadius:
        return Type::Double;
    case Id::Coplanar:
        return Type::Unsigned8;
    case Id::LocalReachabilityDistance:
        return Type::Double;
    case Id::LocalOutlierFactor:
        return Type::Double;
    case Id::Miniball:
        return Type::Double;
    case Id::Reciprocity:
        return Type::Double;
    case Id::Rank:
        return Type::Unsigned8;
    case Id::Eigenvalue0:
        return Type::Double;
    case Id::Eigenvalue1:
        return Type::Double;
    case Id::Eigenvalue2:
        return Type::Double;
    case Id::PlaneFit:
        return Type::Double;
    case Id::RadialDensity:
        return Type::Double;
    case Id::BeamOriginX:
        return Type::Double;
    case Id::BeamOriginY:
        return Type::Double;
    case Id::BeamOriginZ:
        return Type::Double;
    case Id::BeamDirectionX:
        return Type::Double;
    case Id::BeamDirectionY:
        return Type::Double;
    case Id::BeamDirectionZ:
        return Type::Double;
    case Id::NorthPositionRMS:
        return Type::Double;
    case Id::EastPositionRMS:
        return Type::Double;
    case Id::DownPositionRMS:
        return Type::Double;
    case Id::NorthVelocityRMS:
        return Type::Double;
    case Id::EastVelocityRMS:
        return Type::Double;
    case Id::DownVelocityRMS:
        return Type::Double;
    case Id::RollRMS:
        return Type::Double;
    case Id::PitchRMS:
        return Type::Double;
    case Id::HeadingRMS:
        return Type::Double;
    case Id::Reliability:
        return Type::Double;
    case Id::EchoPos:
        return Type::Double;
    case Id::EchoNorm:
        return Type::Double;
    case Id::ImgNbr:
        return Type::Unsigned8;
    case Id::Image:
        return Type::Unsigned16;
    case Id::Dimension:
        return Type::Unsigned8;
    case Id::SphericalRange:
        return Type::Double;
    case Id::SphericalAzimuth:
        return Type::Double;
    case Id::SphericalElevation:
        return Type::Double;
    case Id::H3:
        return Type::Unsigned64;
    case Id::Duplicate:
        return Type::Unsigned8;
    case Id::ShotTimestamp:
        return Type::Double;
    case Id::UnambiguousRange:
        return Type::Double;
    case Id::Unknown:
        throw pdal_error("No type found for undefined dimension.");
    }
    throw pdal_error("No type found for undefined dimension.");
}

/// Check whether a predefined dimension is deprecated.
/// \param[in] id  Dimension ID.
/// \return  True if deprecated, otherwise false.
inline bool isDeprecated(Id id)
{
    switch (id)
    {
    case Id::ClassFlags:
        return true;
    default:
        return false;
    }
}

} // namespace Dimension
} // namespace pdal

