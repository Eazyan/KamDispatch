/* SPDX-License-Identifier: GPL-3.0-or-later
 * Research transient snow scalar on a fixed OpenFOAM mesh and frozen RANS flux.
 * Scenario closure, not a calibrated snow model. No mesh/air feedback.
 */
#include "fvCFD.H"
#include "wallFvPatch.H"
#include <fstream>
#include <iomanip>

int main(int argc, char *argv[])
{
    #include "setRootCase.H"
    #include "createTime.H"
    #include "createMesh.H"
    if (Pstream::parRun()) FatalErrorInFunction << "Serial audit only" << exit(FatalError);
    volScalarField C(IOobject("C", runTime.timeName(), mesh, IOobject::MUST_READ, IOobject::AUTO_WRITE), mesh);
    volVectorField U(IOobject("U", runTime.timeName(), mesh, IOobject::MUST_READ, IOobject::NO_WRITE), mesh);
    volScalarField nut(IOobject("nut", runTime.timeName(), mesh, IOobject::MUST_READ, IOobject::NO_WRITE), mesh);
    volScalarField loading(IOobject("initialBedMass", runTime.timeName(), mesh, IOobject::MUST_READ, IOobject::NO_WRITE), mesh);
    surfaceScalarField phi(IOobject("phi", runTime.timeName(), mesh, IOobject::MUST_READ, IOobject::NO_WRITE), mesh);
    IOdictionary props(IOobject("snowProperties", runTime.constant(), mesh, IOobject::MUST_READ, IOobject::NO_WRITE));
    const scalar ws = readScalar(props.lookup("settlingSpeed"));
    const scalar coeff = readScalar(props.lookup("erosionCoefficient"));
    const scalar threshold = readScalar(props.lookup("thresholdFrictionSpeed"));
    const scalar sc = readScalar(props.lookup("schmidtNumber"));
    const scalar nu = readScalar(props.lookup("kinematicViscosity"));
    if (ws < 0 || coeff < 0 || threshold < 0 || sc <= 0 || nu <= 0)
        FatalErrorInFunction << "Invalid snow properties" << exit(FatalError);
    const label ground = mesh.boundaryMesh().findPatchID("ground");
    if (ground < 0) FatalErrorInFunction << "ground patch required" << exit(FatalError);
    const auto& owners = mesh.boundary()[ground].faceCells();
    const auto& sf = mesh.Sf().boundaryField()[ground];
    scalarField area(mesh.nCells(), Zero), erosionRate(mesh.nCells(), Zero);
    const vectorField gradU(U.boundaryField()[ground].snGrad());
    forAll(owners, f)
    {
        const label cell = owners[f];
        const scalar projectedArea = mag(sf[f].z());
        area[cell] += projectedArea;
        // Kinematic wall stress: (nu + nut) * |dU/dn|, m2/s2.
        const scalar uStar2 = (nu + nut.boundaryField()[ground][f])*mag(gradU[f]);
        erosionRate[cell] += coeff*max(uStar2 - sqr(threshold), scalar(0))*projectedArea;
    }
    volScalarField bed(IOobject("bedMass", runTime.timeName(), mesh, IOobject::NO_READ, IOobject::AUTO_WRITE), mesh,
                       dimensionedScalar("zero", dimMass, Zero));
    // Scene inventory is authoritative kg per column. Mesh coordinate writing
    // slightly rounds face areas; do not silently change initial snow mass.
    forAll(bed, cell) bed[cell] = area[cell] > 0 ? loading[cell] : 0;
    const scalar bedInitial = sum(bed.primitiveField());
    const scalar airInitial = sum(C.primitiveField()*mesh.V());
    scalar boundaryIn=0, boundaryOut=0, eroded=0, deposited=0;
    volVectorField settling(IOobject("settling", runTime.timeName(), mesh, IOobject::NO_READ, IOobject::NO_WRITE), mesh,
                           dimensionedVector("ws", dimVelocity, vector(0,0,-ws)));
    surfaceScalarField transportPhi(IOobject("transportPhi", runTime.timeName(), mesh, IOobject::NO_READ, IOobject::NO_WRITE),
                                    phi + fvc::flux(settling));
    // Walls/symmetries are impermeable; ground settling is the audited exchange.
    forAll(mesh.boundary(), patch)
        if (isA<wallFvPatch>(mesh.boundary()[patch]) || mesh.boundary()[patch].type()=="symmetry")
            transportPhi.boundaryFieldRef()[patch] = 0;
    volScalarField diffusion(IOobject("diffusion", runTime.timeName(), mesh, IOobject::NO_READ, IOobject::NO_WRITE), nut/sc);
    volScalarField source(IOobject("snowSource", runTime.timeName(), mesh, IOobject::NO_READ, IOobject::NO_WRITE), mesh,
                          dimensionedScalar("zero", dimMass/dimVolume/dimTime, Zero));
    mesh.setFluxRequired(C.name());
    std::ofstream ledger((runTime.path()/"snow-ledger.csv").c_str());
    ledger << std::setprecision(17)
           << "time_s,bed_initial_kg,air_initial_kg,bed_final_kg,air_final_kg,boundary_in_kg,boundary_out_kg,explicit_loss_kg,eroded_kg,deposited_kg,residual_kg,min_C_kg_m3,min_bed_kg\n";
    while (runTime.loop())
    {
        const scalar dt = runTime.deltaTValue();
        if (dt <= 0) FatalErrorInFunction << "Positive dt required" << exit(FatalError);
        source = dimensionedScalar("zero", source.dimensions(), Zero);
        forAll(bed, cell)
        {
            const scalar oldBed = bed[cell];
            const scalar oldAir = C[cell]*mesh.V()[cell];
            if (oldBed < 0 || oldAir < 0)
                FatalErrorInFunction << "Negative donor inventory" << exit(FatalError);
            const scalar e = min(oldBed, erosionRate[cell]*dt);
            const scalar d = min(oldAir, ws*C[cell]*area[cell]*dt);
            bed[cell] = (oldBed-e)+d;
            source[cell] = (e-d)/(dt*mesh.V()[cell]);
            eroded += e;
            deposited += d;
        }
        fvScalarMatrix eqn(fvm::ddt(C) + fvm::div(transportPhi, C) - fvm::laplacian(diffusion, C) == source);
        eqn.solve();
        // Exact numerical advective+diffusive boundary flux from this equation.
        const surfaceScalarField flux(eqn.flux());
        forAll(mesh.boundary(), patch)
        {
            const auto& fp = flux.boundaryField()[patch];
            forAll(fp, f)
            {
                boundaryOut += max(fp[f], scalar(0))*dt;
                boundaryIn += max(-fp[f], scalar(0))*dt;
            }
        }
        const scalar b = sum(bed.primitiveField());
        const scalar a = sum(C.primitiveField()*mesh.V());
        const scalar residual = b+a+boundaryOut-bedInitial-airInitial-boundaryIn;
        ledger << runTime.value() << ',' << bedInitial << ',' << airInitial << ',' << b << ',' << a << ','
               << boundaryIn << ',' << boundaryOut << ",0," << eroded << ',' << deposited << ',' << residual << ','
               << min(C.primitiveField()) << ',' << min(bed.primitiveField()) << '\n';
        ledger.flush();
        if (min(C.primitiveField()) < 0 || !std::isfinite(a) || !std::isfinite(b)
            || mag(residual) > 1e-8 + 1e-12*(bedInitial+airInitial+boundaryIn))
            FatalErrorInFunction << "Snow positivity/budget failure: " << residual << exit(FatalError);
        runTime.write();
    }
    Info << "Audited transient snow transport complete" << endl;
    return 0;
}
