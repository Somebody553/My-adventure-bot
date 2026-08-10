class SB:
    name = None
    age = None
    BodyFatPercantage = None
    job = None
    weight = None
    
    def __init__(self, name, age, bfp, job, weight):
        self.set_data(name, age, bfp, job, weight)
        self.get_data()
    
    def set_data(self, name, age, BodyFatPercantage, job, weight):
        self.name = name
        self.age = age
        self.BodyFatPercantage = BodyFatPercantage
        self.job = job
        self.weight = weight
    #def get_data(self):
        #print(self.name, self.age, self.BodyFatPercantage, self.job, self.weight)