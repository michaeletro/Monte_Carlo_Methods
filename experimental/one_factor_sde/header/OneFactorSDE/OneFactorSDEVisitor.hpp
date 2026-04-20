
class OneFactorSDEVisitor
{
    private:

    public:
        virtual void visit(SDETypeD& sde) = 0;
        OneFactorSDEVisitor() = default;
        virtual ~OneFactorSDEVisitor() = default;

        OneFactorSDEVisitor& operator=(const OneFactorSDEVisitor& source) = default;

};

class FDMVisitor : public OneFactorSDEVisitor
{
    private:
        // Parameters for FDM can be added here
    public:
        FDMVisitor() = default;
        void visit(SDETypeD& sde) override {
            // Implement the logic to handle SDETypeD for FDM
            // This is where you would implement the finite difference method for the given SDE
        }
};